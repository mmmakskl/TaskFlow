import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.config.settings import Settings
from app.db.seed import seed
from app.models import AuditEvent, Notification, Penalty, PenaltyType, ScoreEvent, User
from app.repositories.base import Repository
from app.repositories.results import ResultRepository
from app.repositories.tasks import TaskRepository
from app.services.notifications import NotificationService
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.rules import DomainError, StaleAction
from app.services.tasks import TaskService
from app.utils.time import utcnow

pytestmark = pytest.mark.integration


async def test_flow_a_peer_rating_and_completion_snapshot(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        repo = TaskRepository(session)
        max_user, vika = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(max_user, "Купить билеты", "flow-a")
        assert task.status == "BACKLOG" and task.rated_points is None
        assert (await repo.pending_next(vika))[1] == 1
        with pytest.raises(DomainError):
            await service.rate(max_user, task.id, 800, task.version)
        await service.rate(vika, task.id, 800, task.version)
        assert task.status == "TODO"
        before = await ResultService(ResultRepository(session), settings).week()
        assert before[1][0][1] == 0
        await service.change_status(max_user, task.id, "DONE", task.version)
        assert task.awarded_points == 800 and task.completed_at is not None
        # Snapshot remains independent of mutable rating field.
        task.rated_points = 900
        await session.flush()
        after = await ResultService(ResultRepository(session), settings).week()
        assert after[1][0][1] == 800


async def test_flow_b_batching_and_persistent_cooldown(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, evaluator = await repo.users()
        service = TaskService(repo, settings)
        for i in range(10):
            await service.create(owner, f"Task {i}", f"batch-{i}")
        assert await session.scalar(select(func.count(Notification.id))) == 1
        await session.execute(
            update(Notification).values(available_at=utcnow() - timedelta(seconds=1))
        )

    async def claim():
        async with factory.begin() as session:
            return await NotificationService(Repository(session), settings).claim()

    claims = await asyncio.gather(claim(), claim())
    assert sum(claimed is not None for claimed in claims) == 1
    assert next(c for c in claims if c is not None)[2] == 10
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, evaluator = await repo.users()
        assert evaluator.last_rating_notification_at is not None
        await TaskService(repo, settings).create(owner, "One more", "batch-11")
        await session.execute(
            update(Notification)
            .where(Notification.status == "pending")
            .values(available_at=utcnow() - timedelta(seconds=1))
        )
        assert await NotificationService(repo, settings).claim() is None


async def test_flows_c_d_penalty_snapshot_and_rate_change(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        repo = Repository(session)
        max_user, vika = await repo.users()
        service = PenaltyService(repo)
        instagram = await session.scalar(
            select(PenaltyType).where(PenaltyType.seed_key == "instagram")
        )
        preview = await service.preview(instagram.id, 12, None)
        old = await service.create(vika, max_user.id, preview, "penalty-old", True)
        await service.configure(vika, instagram.id, instagram.version, rate=150)
        new_preview = await service.preview(instagram.id, 12, None)
        new = await service.create(vika, max_user.id, new_preview, "penalty-new", True)
        assert old.rate_snapshot == 100 and old.points == 1200
        assert new.rate_snapshot == 150 and new.points == 1800
        result = await ResultService(ResultRepository(session), settings).week()
        assert result[1][0][1] - result[1][0][2] == -3000
        await service.delete(vika, old.id, old.version, True)
        result = await ResultService(ResultRepository(session), settings).week()
        assert result[1][0][2] == 1800


async def test_flow_e_reopen_reverses_original_week_and_audits(
    factory, settings: Settings, monkeypatch
) -> None:
    last_week = utcnow() - timedelta(days=7)
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: last_week)
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, evaluator = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Task", "reopen")
        await service.rate(evaluator, task.id, 500, task.version)
        await service.change_status(owner, task.id, "DONE", task.version)
        result = ResultService(ResultRepository(session), settings)
        assert (await result.week(-1))[1][0][1] == 500
        with pytest.raises(DomainError):
            await service.change_status(owner, task.id, "TODO", task.version)
        await service.change_status(owner, task.id, "TODO", task.version, True)
        assert (await result.week(-1))[1][0][1] == 0
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "status").order_by(AuditEvent.id.desc())
        )
        assert audit.old_data["status"] == "DONE"


async def test_concurrent_duplicate_completion_awards_once(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, evaluator = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Task", "concurrent-done")
        await service.rate(evaluator, task.id, 600, task.version)
        task_id, version, owner_id = task.id, task.version, owner.id

    async def complete():
        try:
            async with factory.begin() as session:
                repo = TaskRepository(session)
                owner = await repo.get(User, owner_id)
                await TaskService(repo, settings).change_status(owner, task_id, "DONE", version)
            return True
        except StaleAction:
            return False

    assert sum(await asyncio.gather(complete(), complete())) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 1
        assert await session.scalar(select(func.sum(ScoreEvent.points))) == 600


async def test_concurrent_duplicate_penalty_only_one_record(factory, settings: Settings) -> None:
    async def create():
        async with factory.begin() as session:
            repo = Repository(session)
            owner, actor = await repo.users()
            kind = await session.scalar(
                select(PenaltyType).where(PenaltyType.seed_key == "instagram")
            )
            service = PenaltyService(repo)
            penalty = await service.create(
                actor, owner.id, await service.preview(kind.id, 5, None), "same-operation", True
            )
            return penalty.id

    ids = await asyncio.gather(create(), create())
    assert ids[0] == ids[1]
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 1
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 1


async def test_seed_rerun_preserves_modified_rate_and_settings(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        kind.rate = 150
        user = await session.scalar(select(User).where(User.role == "max"))
        user.weekly_target = 12000
        await session.flush()
        await seed(session, settings)
        assert await session.scalar(select(func.count(PenaltyType.id))) == 7
        assert await session.scalar(select(func.count(User.id))) == 2
        await session.refresh(kind)
        await session.refresh(user)
        assert kind.rate == 150 and user.weekly_target == 12000


async def test_events_are_database_immutable(factory, settings: Settings) -> None:
    async with factory.begin() as session:
        owner = await session.scalar(select(User).where(User.role == "max"))
        event = ScoreEvent(
            user_id=owner.id,
            category="task",
            points=1,
            effective_at=utcnow(),
            operation_key="immutable",
        )
        session.add(event)
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            await session.execute(update(ScoreEvent).values(points=1000))
