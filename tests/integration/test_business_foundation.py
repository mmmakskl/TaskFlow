import asyncio

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.models import AuditEvent, Notification, Penalty, PenaltyType, ScoreEvent, Task, User
from app.repositories.base import Repository
from app.repositories.tasks import TaskRepository
from app.services.penalties import PenaltyService
from app.services.rules import DomainError, StaleAction
from app.services.tasks import TaskService

pytestmark = pytest.mark.integration


async def test_service_savepoint_survives_caught_mid_operation_failure(
    factory, settings, monkeypatch
):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, evaluator = await repo.users()
        service = TaskService(repo, settings)

        async def fail(*args):
            raise RuntimeError("injected outbox failure")

        with monkeypatch.context() as patch:
            patch.setattr(repo, "enqueue_rating", fail)
            with pytest.raises(RuntimeError):
                await service.create(owner, "Must disappear", "failed")
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(select(func.count(AuditEvent.id))) == 0
        task = await service.create(owner, "Survives", "success")
        await service.rate(evaluator, task.id, 500, task.version)

        def audit_failure(*args):
            raise RuntimeError("injected audit failure")

        with monkeypatch.context() as patch:
            patch.setattr(repo, "audit", audit_failure)
            with pytest.raises(RuntimeError):
                await service.change_status(owner, task.id, "DONE", task.version)
        await session.refresh(task)
        assert task.status == "TODO" and task.awarded_points is None
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        await service.change_status(owner, task.id, "DONE", task.version)
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 1
        assert await session.scalar(select(func.sum(ScoreEvent.points))) == 500


async def test_penalty_configuration_and_creation_atomicity(factory, settings, monkeypatch):
    async with factory.begin() as session:
        repo = Repository(session)
        owner, actor = await repo.users()
        service = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        with pytest.raises(DomainError):
            await service.configure(actor, kind.id, kind.version, name="Changed", rate=0)
        await session.refresh(kind)
        assert kind.name == "Instagram" and kind.rate == 100 and kind.version == 1
        draft = await service.preview(kind.id, 5, None)

        def fail(*args):
            raise RuntimeError("injected audit failure")

        with monkeypatch.context() as patch:
            patch.setattr(service.repo, "audit", fail)
            with pytest.raises(RuntimeError):
                await service.create(actor, owner.id, draft, "failed", True)
        assert await session.scalar(select(func.count(Penalty.id))) == 0
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        await service.create(actor, owner.id, draft, "success", True)
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 1


@pytest.mark.parametrize("action", ["rate", "reopen", "cancel", "delete", "configure"])
async def test_concurrent_mutations_have_one_winner(factory, settings, action):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Race", "race")
        if action in ("reopen", "cancel"):
            await service.rate(peer, task.id, 500, task.version)
            await service.change_status(owner, task.id, "DONE", task.version)
        entity_id, version, actor_id = (
            task.id,
            task.version,
            peer.id if action == "rate" else owner.id,
        )
        if action in ("delete", "configure"):
            penalty_service = PenaltyService(repo, settings)
            kind = await session.scalar(
                select(PenaltyType).where(PenaltyType.seed_key == "instagram")
            )
            if action == "configure":
                entity_id, version = kind.id, kind.version
            else:
                penalty = await penalty_service.create(
                    owner,
                    owner.id,
                    await penalty_service.preview(kind.id, 5, None),
                    "penalty",
                    True,
                )
                entity_id, version = penalty.id, penalty.version

    async def mutate():
        try:
            async with factory.begin() as session:
                repo = TaskRepository(session)
                actor = await repo.get(User, actor_id)
                if action == "rate":
                    await TaskService(repo, settings).rate(actor, entity_id, 500, version)
                elif action in ("reopen", "cancel"):
                    await TaskService(repo, settings).change_status(
                        actor,
                        entity_id,
                        "TODO" if action == "reopen" else "CANCELLED",
                        version,
                        True,
                    )
                elif action == "delete":
                    await PenaltyService(repo, settings).delete(actor, entity_id, version, True)
                else:
                    await PenaltyService(repo, settings).configure(
                        actor, entity_id, version, rate=150
                    )
            return True
        except StaleAction:
            return False

    assert sum(await asyncio.gather(mutate(), mutate(), mutate())) == 1
    async with factory() as session:
        events = list(await session.scalars(select(ScoreEvent)))
        if action in ("reopen", "cancel", "delete"):
            assert len(events) == 2 and sum(event.points for event in events) == 0
        if action == "rate":
            assert (
                await session.scalar(
                    select(func.count(Notification.id)).where(Notification.kind == "rated")
                )
                == 1
            )


async def test_authenticated_identity_is_enforced_without_middleware(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        with pytest.raises(DomainError):
            await TaskService(repo, settings).create(
                User(id=peer.id, telegram_id=owner.telegram_id), "Forged", "forged"
            )
        with pytest.raises(DomainError):
            await TaskService(repo, settings).create(
                User(id=owner.id, telegram_id=999), "Unknown", "unknown"
            )
        assert await session.scalar(select(func.count(Task.id))) == 0


@pytest.mark.parametrize(
    "model,mutation",
    [
        (ScoreEvent, "update"),
        (ScoreEvent, "delete"),
        (AuditEvent, "update"),
        (AuditEvent, "delete"),
    ],
)
async def test_ledger_and_audit_cannot_be_rewritten(factory, settings, model, mutation):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Task", "immutable")
        await service.rate(peer, task.id, 500, task.version)
        await service.change_status(owner, task.id, "DONE", task.version)
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            statement = (
                delete(model)
                if mutation == "delete"
                else update(model).values(
                    **({"points": 999} if model is ScoreEvent else {"action": "changed"})
                )
            )
            await session.execute(statement)


@pytest.mark.parametrize(
    "field,value",
    [
        ("points", 999),
        ("rate_snapshot", 200),
        ("name_snapshot", "New"),
        ("description_snapshot", "New"),
        ("quantity", 6),
        ("comment", "Changed"),
    ],
)
async def test_penalty_snapshot_is_database_immutable(factory, settings, field, value):
    async with factory.begin() as session:
        repo = Repository(session)
        owner, actor = await repo.users()
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        service = PenaltyService(repo, settings)
        await service.create(
            actor, owner.id, await service.preview(kind.id, 5, None), "snapshot", True
        )
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            await session.execute(update(Penalty).values(**{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "TODO"},
        {"awarded_points": 500},
        {"rated_points": 500},
        {"status": "CANCELLED"},
        {"version": 0},
    ],
)
async def test_database_rejects_incoherent_task(factory, settings, changes):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner = (await repo.users())[0]
        await TaskService(repo, settings).create(owner, "Task", "guard")
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            await session.execute(update(Task).values(**changes))


async def test_query_indexes_present_and_used(factory):
    async with factory() as session:
        names = set(
            await session.scalars(
                text("SELECT indexname FROM pg_indexes WHERE schemaname='taskflow'")
            )
        )
        expected = {
            "ix_tasks_active_owner_id",
            "ix_tasks_pending_evaluator_id",
            "ix_penalties_live_id",
            "ix_penalty_types_order",
            "ix_score_effective_user",
        }
        assert expected <= names
        await session.execute(text("SET LOCAL enable_seqscan=off"))
        plan = " ".join(
            await session.scalars(
                text(
                    "EXPLAIN SELECT id FROM taskflow.tasks WHERE evaluator_user_id=2 AND deleted_at IS NULL AND status='BACKLOG' ORDER BY id LIMIT 1"
                )
            )
        )
        assert "ix_tasks_pending_evaluator_id" in plan


async def test_cross_week_ledger_matches_current_tasks_and_penalties(
    factory, settings, monkeypatch
):
    from datetime import timedelta

    from app.repositories.results import ResultRepository
    from app.services.results import ResultService
    from app.utils.time import utcnow, week_bounds

    now = utcnow()
    previous = now - timedelta(days=7)
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        results = ResultService(ResultRepository(session), settings)
        task = await service.create(owner, "Cross week", "cross-week")
        await service.rate(peer, task.id, 800, task.version)
        with monkeypatch.context() as patch:
            patch.setattr("app.services.tasks.utcnow", lambda: previous)
            await service.change_status(owner, task.id, "DONE", task.version)
        assert (await results.week(-1, now))[1][0][1] == 800
        await service.change_status(owner, task.id, "TODO", task.version, True)
        assert (await results.week(-1, now))[1][0][1] == 0
        await service.change_status(owner, task.id, "DONE", task.version)
        penalties = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        add = penalties.repo.add

        def add_historical(entity):
            if isinstance(entity, Penalty):
                entity.created_at = previous
            add(entity)

        with monkeypatch.context() as patch:
            patch.setattr(penalties.repo, "add", add_historical)
            penalty = await penalties.create(
                peer, owner.id, await penalties.preview(kind.id, 12, None), "historical", True
            )
        assert (await results.week(-1, now))[1][0][2] == 1200
        await penalties.delete(owner, penalty.id, penalty.version, True)
        assert (await results.week(-1, now))[1][0][2] == 0
        for offset in (-1, 0):
            start, end = week_bounds(now, settings.default_timezone, offset)
            earned = await session.scalar(
                select(func.coalesce(func.sum(Task.awarded_points), 0)).where(
                    Task.status == "DONE", Task.completed_at >= start, Task.completed_at < end
                )
            )
            deducted = await session.scalar(
                select(func.coalesce(func.sum(Penalty.points), 0)).where(
                    Penalty.deleted_at.is_(None),
                    Penalty.created_at >= start,
                    Penalty.created_at < end,
                )
            )
            score = (await results.week(offset, now))[1][0]
            assert score[1] - score[2] == earned - deducted


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": None},
        {"quantity": 0},
        {"rate_snapshot": 0},
        {"points": 1},
        {"calculation_type_snapshot": "UNKNOWN"},
    ],
)
async def test_database_rejects_invalid_penalty_snapshot(factory, settings, changes):
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            repo = Repository(session)
            owner, actor = await repo.users()
            kind = await session.scalar(
                select(PenaltyType).where(PenaltyType.seed_key == "instagram")
            )
            fields = dict(
                target_user_id=owner.id,
                created_by_user_id=actor.id,
                penalty_type_id=kind.id,
                name_snapshot=kind.name,
                description_snapshot=kind.description,
                calculation_type_snapshot="PER_MINUTE",
                rate_snapshot=100,
                quantity=5,
                points=500,
                operation_key="invalid",
            )
            session.add(Penalty(**{**fields, **changes}))
            await session.flush()


async def test_repository_pagination_is_bounded_and_scoped(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        for i in range(12):
            await service.create(owner, f"Task {i}", f"page-{i}")
        await service.create(peer, "Other owner", "other-owner")
        rows, count = await repo.list(owner, page=0)
        assert len(rows) == 5 and count == 12
        assert all(row.owner_user_id == owner.id for row in rows)
        rows, count = await repo.list(owner, page=99)
        assert len(rows) == 2 and count == 12
        pending, count = await repo.list(peer, pending=True)
        assert len(pending) == 5 and count == 12
        first, count = await repo.pending_next(peer, after_id=999999)
        assert first.title == "Task 0" and count == 12
        for page in (True, -1, 100001, "1", 1.5):
            with pytest.raises(DomainError):
                await repo.list(owner, page=page)
        with pytest.raises(DomainError):
            await repo.pending_next(peer, after_id=True)


async def test_penalty_repository_pages_and_inactive_type_filter(factory, settings):
    async with factory.begin() as session:
        repo = Repository(session)
        owner, peer = await repo.users()
        service = PenaltyService(repo, settings)
        rows, more = await service.types()
        assert len(rows) == 5 and more
        rows, more = await service.types(page=1)
        assert len(rows) == 2 and not more
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        for i in range(7):
            await service.create(
                peer, owner.id, await service.preview(kind.id, 5, None), f"penalty-page-{i}", True
            )
        first, more = await service.list()
        assert len(first) == 5 and more and first[0].id > first[-1].id
        last, more = await service.list(1)
        assert len(last) == 2 and not more
        await service.delete(owner, first[0].id, first[0].version, True)
        assert (await service.list())[0][0].id == first[1].id
        await service.configure(owner, kind.id, kind.version, is_active=False)
        active = []
        for page in (0, 1):
            active.extend((await service.types(page=page))[0])
        assert len(active) == 6 and kind.id not in [row.id for row in active]
        assert len((await service.types(include_inactive=True, page=1))[0]) == 2
        for invalid in (True, -1, 100001, "1"):
            with pytest.raises(DomainError):
                await service.list(invalid)
            with pytest.raises(DomainError):
                await service.types(page=invalid)
