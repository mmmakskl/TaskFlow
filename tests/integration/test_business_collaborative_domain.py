import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.seed import seed
from app.models import ScoreEvent, Task, TaskTarget, TaskTemplate, User
from app.repositories.tasks import TaskRepository
from app.repositories.templates import TaskTemplateRepository
from app.services.rules import StaleAction
from app.services.tasks import TaskService
from app.services.templates import TaskTemplateService
from tests.integration.test_business_migrations import alembic

pytestmark = pytest.mark.integration


async def test_shared_concurrent_completion_reopen_and_summaries(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Shared", "shared", target=TaskTarget.SHARED)
        await service.rate(peer, task.id, 500, task.version)
        task_id, version, owner_id, peer_id = task.id, task.version, owner.id, peer.id

    async def finish(user_id):
        async with factory.begin() as session:
            actor = await session.get(User, user_id)
            try:
                await TaskService(TaskRepository(session), settings).change_status(
                    actor, task_id, "DONE", version
                )
                return True
            except StaleAction:
                return False

    assert sorted(await asyncio.gather(finish(owner_id), finish(peer_id))) == [False, True]
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 2
        assert (await repo.summary(owner)).completed_points == 500
        assert (await repo.summary(owner, scope="peer")).completed_points == 500
        assert (await repo.list(owner, scope="peer"))[1] == 1
        task = await session.get(Task, task_id)
        await TaskService(repo, settings).change_status(peer, task.id, "TODO", task.version, True)
        assert (await repo.summary(owner)).completed_points == 0
        summary = await repo.summary(owner)
        assert summary.todo_points == summary.planned_points == 500
        assert summary.in_progress_points == 0
        await session.flush()
        events = list(await session.scalars(select(ScoreEvent)))
        assert len(events) == 4 and len({e.effective_at for e in events}) == 1


async def test_rerate_concurrency_and_immutable_identity(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        task = await TaskService(repo, settings).create(owner, "Work", "work")
        await TaskService(repo, settings).rate(peer, task.id, 100, task.version)
        task_id, version, peer_id = task.id, task.version, peer.id

    async def rerate(points):
        async with factory.begin() as session:
            actor = await session.get(User, peer_id)
            try:
                await TaskService(TaskRepository(session), settings).rerate(
                    actor, task_id, points, version, True
                )
                return True
            except StaleAction:
                return False

    assert sorted(await asyncio.gather(rerate(200), rerate(300))) == [False, True]
    async with factory.begin() as session:
        for expression in (
            "creator_user_id=evaluator_user_id",
            "owner_user_id=evaluator_user_id",
            "first_accepted_at=NULL",
            "operation_key='rewritten'",
        ):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(f"UPDATE taskflow.tasks SET {expression}"))
        task = await session.get(Task, task_id)
        assert task.rated_points in (200, 300) and task.status == "TODO"


async def test_templates_seed_snapshots_and_scope_whole_user_totals(factory, settings):
    async with factory.begin() as session:
        await seed(session, settings)
        assert await session.scalar(select(func.count(TaskTemplate.id))) == 2
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        template = await session.scalar(
            select(TaskTemplate).where(TaskTemplate.seed_key == "english")
        )
        for index in range(8):
            await service.create(
                owner,
                template.title,
                f"auto:{index}",
                estimate=60,
                proposed_points=500,
                template_id=template.id,
                template_version=template.version,
            )
        await service.create(owner, "Peer offer", "peer", target=TaskTarget.PEER)
        tasks, count = await repo.list(owner, "all", 1)
        assert len(tasks) == 3 and count == 8
        summary = await repo.summary(owner)
        assert summary.todo_points == 4000 and summary.pending_count == 0
        assert (await repo.summary(owner, "peer")).pending_count == 1
        await TaskTemplateService(TaskTemplateRepository(session), settings).edit(
            peer, template.id, template.version, points=700
        )
        assert all(t.template_snapshot["points"] == 500 for t in tasks)
        assert all(t.rated_points == 500 for t in tasks)


async def test_populated_0004_upgrade_preserves_awards_and_backfills(factory, settings):
    async with factory.begin() as session:
        await session.execute(text("DROP SCHEMA taskflow CASCADE"))
        await session.execute(text("DROP TABLE public.alembic_version"))
    assert (await alembic(settings, "upgrade", "0004"))[0] == 0
    async with factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO taskflow.users (id,telegram_id,display_name,role,timezone,weekly_target,created_at,updated_at) VALUES (1,1001,'Max','max','UTC',10000,now(),now()),(2,1002,'Vika','vika','UTC',10000,now(),now())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO taskflow.tasks (owner_user_id,evaluator_user_id,title,status,rated_points,rated_by_user_id,rated_at,completed_at,awarded_points,operation_key,version,created_at,updated_at) VALUES (1,2,'Legacy','DONE',500,2,now(),now(),500,'legacy',3,now(),now())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO taskflow.score_events (user_id,category,points,effective_at,operation_key,created_at) VALUES (1,'task',500,now(),'legacy:score',now())"
            )
        )
        before = (await session.execute(text("SELECT * FROM taskflow.score_events"))).all()
    code, output = await alembic(settings, "upgrade", "head")
    assert code == 0, output
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.creator_user_id == task.owner_user_id == task.proposal_by_user_id == 1
        assert task.participant_user_ids == (1,)
        assert task.rating_source == "MANUAL" and task.first_accepted_at == task.rated_at
        assert task.awarded_points == 500 and task.kind == "PERSONAL"
        assert (await session.execute(text("SELECT * FROM taskflow.score_events"))).all() == before
    assert (await alembic(settings, "downgrade", "0004"))[0] != 0


async def test_summary_is_alltime_net_task_ledger_not_penalties_or_filters(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        session.add_all(
            [
                ScoreEvent(
                    user_id=owner.id,
                    category="task",
                    points=900,
                    effective_at=datetime(2020, 1, 1, tzinfo=UTC),
                    operation_key="historic",
                ),
                ScoreEvent(
                    user_id=owner.id,
                    category="task",
                    points=-200,
                    effective_at=datetime(2020, 1, 1, tzinfo=UTC),
                    operation_key="historic-reopen",
                ),
                ScoreEvent(
                    user_id=owner.id,
                    category="penalty",
                    points=-500,
                    effective_at=datetime(2020, 1, 1, tzinfo=UTC),
                    operation_key="historic-penalty",
                ),
            ]
        )
        await session.flush()
        await repo.list(owner, "archive", page=100)
        assert (await repo.summary(owner)).completed_points == 700
        assert (await repo.summary(owner, "peer")).completed_points == 0


async def test_task_scopes_exclude_unconfigured_participants(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        rogue = User(
            telegram_id=9999, role="rogue", display_name="Rogue", timezone="UTC", weekly_target=1
        )
        session.add(rogue)
        await session.flush()
        session.add(
            Task(
                creator_user_id=rogue.id,
                owner_user_id=rogue.id,
                evaluator_user_id=owner.id,
                proposal_by_user_id=rogue.id,
                title="Hidden",
                operation_key="rogue",
            )
        )
        await session.flush()
        assert (await repo.list(owner, scope="peer"))[1] == 0
        assert (await repo.list(owner, scope="all"))[1] == 0
        assert (await repo.pending_next(owner))[1] == 0
        assert (await repo.summary(owner, "peer")).pending_count == 0
        session.add(
            Task(
                creator_user_id=owner.id,
                owner_user_id=owner.id,
                evaluator_user_id=peer.id,
                proposal_by_user_id=rogue.id,
                title="Rogue proposal author",
                operation_key="rogue-proposal",
            )
        )
        await session.flush()
        assert (await repo.list(owner, scope="mine"))[1] == 0
        assert (await repo.list(peer, pending=True))[1] == 0
        assert (await repo.pending_next(peer))[1] == 0
        assert (await repo.summary(owner)).pending_count == 0
        session.add(
            Task(
                creator_user_id=owner.id,
                owner_user_id=owner.id,
                evaluator_user_id=rogue.id,
                rated_by_user_id=rogue.id,
                proposal_by_user_id=owner.id,
                title="Rogue rating author",
                operation_key="rogue-rating",
                status="TODO",
                rated_points=900,
                rating_source="MANUAL",
                rated_at=datetime.now(UTC),
                first_accepted_at=datetime.now(UTC),
            )
        )
        await session.flush()
        assert (await repo.list(owner, scope="mine"))[1] == 0
        assert (await repo.summary(owner)).todo_points == 0


async def test_direct_task_boundaries_reject_rogue_records_without_side_effects(factory, settings):
    from app.models import AuditEvent, Notification
    from app.services.rules import DomainError

    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        rogue = User(
            telegram_id=9999, role="rogue", display_name="Rogue", timezone="UTC", weekly_target=1
        )
        session.add(rogue)
        await session.flush()
        pending = Task(
            creator_user_id=rogue.id,
            owner_user_id=rogue.id,
            evaluator_user_id=owner.id,
            proposal_by_user_id=rogue.id,
            title="Hidden pending",
            operation_key="rogue-pending",
        )
        active = Task(
            creator_user_id=owner.id,
            owner_user_id=owner.id,
            shared_user_id=rogue.id,
            kind="SHARED",
            evaluator_user_id=peer.id,
            proposal_by_user_id=owner.id,
            title="Hidden shared",
            operation_key="rogue-shared",
            status="TODO",
            rated_points=500,
            rated_by_user_id=peer.id,
            rated_at=datetime.now(UTC),
            rating_source="MANUAL",
            first_accepted_at=datetime.now(UTC),
        )
        session.add_all([pending, active])
        await session.flush()
        service = TaskService(repo, settings)
        before = [
            await session.scalar(select(func.count(model.id)))
            for model in (AuditEvent, Notification, ScoreEvent)
        ]
        actions = [
            lambda: service.view(owner, pending.id),
            lambda: service.rate(owner, pending.id, 500, pending.version),
            lambda: service.reject(owner, pending.id, pending.version, True),
            lambda: service.view(owner, active.id),
            lambda: service.change_status(owner, active.id, "DONE", active.version),
            lambda: service.edit(owner, active.id, active.version, title="Changed"),
            lambda: service.rerate(peer, active.id, 600, active.version, True),
            lambda: service.create(owner, "Retry", "rogue-shared"),
        ]
        for action in actions:
            with pytest.raises(DomainError):
                await action()
        assert (
            pending.status == "BACKLOG" and active.status == "TODO" and active.rated_points == 500
        )
        assert active.title == "Hidden shared" and active.version == 1
        assert [
            await session.scalar(select(func.count(model.id)))
            for model in (AuditEvent, Notification, ScoreEvent)
        ] == before
        template = TaskTemplate(
            creator_user_id=rogue.id,
            title="Rogue template",
            points=500,
            estimated_minutes=60,
            operation_key="rogue-template",
        )
        session.add(template)
        await session.flush()
        templates = TaskTemplateService(TaskTemplateRepository(session), settings)
        with pytest.raises(DomainError):
            await templates.view(owner, template.id)
        with pytest.raises(DomainError):
            await templates.edit(peer, template.id, template.version, points=900)
        with pytest.raises(DomainError):
            await service.create(
                owner,
                template.title,
                "rogue-template-task",
                estimate=60,
                proposed_points=500,
                template_id=template.id,
                template_version=1,
            )
        assert await templates.count(owner) == 2
        assert template.id not in [row.id for row in await templates.list(owner)]


async def test_malformed_template_snapshots_and_unknown_checks_reject(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        template = await session.scalar(select(TaskTemplate).limit(1))
        valid = {"title": "English", "points": 500, "estimated_minutes": 60}
        invalid = [
            (None, valid),
            (0, valid),
            (1, {}),
            (1, {"title": "English", "points": 500}),
            (1, {"title": "English", "points": None, "estimated_minutes": 60}),
            (1, {"title": None, "points": 500, "estimated_minutes": 60}),
            (1, {"title": "English", "points": "500", "estimated_minutes": 60}),
            (1, {"title": "English", "points": 500, "estimated_minutes": "60"}),
            (1, {"title": "English", "points": 500, "estimated_minutes": 0}),
            (1, {"title": "English", "points": 10001, "estimated_minutes": 60}),
            (1, []),
            (1, None),
        ]
        for index, (version, snapshot) in enumerate(invalid):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    session.add(
                        Task(
                            creator_user_id=owner.id,
                            owner_user_id=owner.id,
                            evaluator_user_id=peer.id,
                            proposal_by_user_id=owner.id,
                            title="English",
                            estimated_minutes=60,
                            status="TODO",
                            rated_points=500,
                            rated_at=datetime.now(UTC),
                            rating_source="TEMPLATE",
                            first_accepted_at=datetime.now(UTC),
                            template_id=template.id,
                            template_version=version,
                            template_snapshot=snapshot,
                            operation_key=f"malformed:{index}",
                        )
                    )
                    await session.flush()
        assert await session.scalar(select(func.count(Task.id))) == 0
        valid_task = await TaskService(repo, settings).create(
            owner,
            template.title,
            "valid-auto",
            estimate=template.estimated_minutes,
            proposed_points=template.points,
            template_id=template.id,
            template_version=template.version,
        )
        assert valid_task.status == "TODO"


@pytest.mark.parametrize("target", [TaskTarget.PEER, TaskTarget.SHARED])
async def test_previous_rater_semantic_edit_reapproves_without_partial_autoflush(
    factory, settings, target
):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        author, recipient = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(author, "Accepted", "semantic-autoflush", target=target)
        await service.rate(recipient, task.id, 500, task.version)
        accepted_at = task.first_accepted_at
        identities = (task.creator_user_id, task.owner_user_id, task.shared_user_id)
        await service.change_status(recipient, task.id, "IN_PROGRESS", task.version)
        await service.rerate(recipient, task.id, 750, task.version, confirmed=True)
        await service.edit(recipient, task.id, task.version, title="Changed by former rater")
        await session.flush()
        assert task.status == "BACKLOG" and task.proposed_points == 750
        assert task.proposal_by_user_id == recipient.id and task.evaluator_user_id == author.id
        assert (
            task.rated_points
            is task.rated_by_user_id
            is task.rating_source
            is task.started_at
            is None
        )
        assert task.first_accepted_at == accepted_at
        assert identities == (task.creator_user_id, task.owner_user_id, task.shared_user_id)
        await service.rate(author, task.id, 800, task.version)
        assert (
            task.status == "TODO"
            and task.rated_points == 800
            and task.first_accepted_at == accepted_at
        )


@pytest.mark.parametrize("has_author", [False, True])
async def test_populated_rating_requires_explicit_source(factory, settings, has_author):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        with pytest.raises(DBAPIError, match="ck_tasks_rating_complete"):
            async with session.begin_nested():
                session.add(
                    Task(
                        creator_user_id=owner.id,
                        owner_user_id=owner.id,
                        evaluator_user_id=peer.id,
                        proposal_by_user_id=owner.id,
                        title="Missing provenance",
                        operation_key="missing-source",
                        status="TODO",
                        rated_points=500,
                        rated_by_user_id=peer.id if has_author else None,
                        rated_at=datetime.now(UTC),
                        first_accepted_at=datetime.now(UTC),
                    )
                )
                await session.flush()
        assert await session.scalar(select(func.count(Task.id))) == 0


async def test_complete_rating_shapes_remain_valid(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        unrated = await service.create(owner, "Unrated", "unrated-shape")
        manual = await service.create(owner, "Manual", "manual-shape")
        await service.rate(peer, manual.id, 500, manual.version)
        template = await session.scalar(select(TaskTemplate).limit(1))
        automatic = await service.create(
            owner,
            template.title,
            "template-shape",
            estimate=template.estimated_minutes,
            proposed_points=template.points,
            template_id=template.id,
            template_version=template.version,
        )
        await session.flush()
        assert unrated.rating_source is None and unrated.rated_points is None
        assert manual.rating_source == "MANUAL" and manual.rated_by_user_id == peer.id
        assert automatic.rating_source == "TEMPLATE" and automatic.rated_by_user_id is None


@pytest.mark.parametrize("rogue_shape", ["identity", "template"])
async def test_ineligible_tasks_hidden_and_rating_batches_skip_without_cooldown(
    factory, settings, rogue_shape
):
    from app.models import Notification
    from app.repositories.notifications import NotificationRepository
    from app.services.notifications import NotificationService
    from app.services.rules import DomainError

    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        rogue = User(
            telegram_id=9999, role="rogue", display_name="Rogue", timezone="UTC", weekly_target=1
        )
        session.add(rogue)
        await session.flush()
        template_fields = {}
        if rogue_shape == "template":
            template = TaskTemplate(
                creator_user_id=rogue.id,
                title="Hidden template",
                points=500,
                operation_key="rogue-template-visibility",
            )
            session.add(template)
            await session.flush()
            template_fields = {
                "template_id": template.id,
                "template_version": template.version,
                "template_snapshot": {
                    "title": template.title,
                    "points": template.points,
                    "estimated_minutes": None,
                },
            }
        creator_id = rogue.id if rogue_shape == "identity" else owner.id
        hidden = Task(
            creator_user_id=creator_id,
            owner_user_id=creator_id,
            proposal_by_user_id=creator_id,
            evaluator_user_id=peer.id,
            title="Hidden request",
            operation_key="hidden-request",
            **template_fields,
        )
        session.add(hidden)
        # A separately persisted rated row must also stay out of plan totals.
        session.add(
            Task(
                creator_user_id=creator_id,
                owner_user_id=creator_id,
                proposal_by_user_id=creator_id,
                evaluator_user_id=peer.id,
                title="Hidden active task",
                operation_key="hidden-active",
                status="TODO",
                rated_points=500,
                rated_by_user_id=peer.id,
                rated_at=datetime.now(UTC),
                rating_source="MANUAL",
                first_accepted_at=datetime.now(UTC),
                **template_fields,
            )
        )
        await session.flush()
        for actor in (owner, peer):
            for scope in ("mine", "peer", "shared", "assigned", "all"):
                assert (await repo.list(actor, scope=scope))[1] == 0
                summary = await repo.summary(actor, scope)
                assert summary.pending_count == summary.planned_points == 0
            assert (await repo.list(actor, pending=True))[1] == 0
            assert await repo.pending_next(actor) == (None, 0)
        service = TaskService(repo, settings)
        with pytest.raises(DomainError):
            await service.view(owner, hidden.id)
        with pytest.raises(DomainError):
            await service.rate(peer, hidden.id, 500, hidden.version)
        notifications = NotificationRepository(session)
        assert await notifications.pending_count(peer.id) == 0
        await notifications.enqueue_rating(peer.id, 0)
        before = peer.last_rating_notification_at
        assert await NotificationService(notifications, settings).claim() is None
        batch = await session.scalar(
            select(Notification).where(Notification.kind == "rating_pending")
        )
        assert batch.status == "skipped" and batch.attempts == 0
        assert peer.last_rating_notification_at == before
        # Queue a due batch before creation, which preserves its existing due time.
        await notifications.enqueue_rating(peer.id, 0)
        await service.create(owner, "Eligible request", "eligible-request")
        assert await notifications.pending_count(peer.id) == 1
        claim = await NotificationService(notifications, settings).claim()
        assert claim is not None and claim[2] == 1 and claim[0].attempts == 1
        assert peer.last_rating_notification_at is not None


async def test_edited_disabled_template_tasks_remain_visible_and_actionable(factory, settings):
    from app.repositories.notifications import NotificationRepository

    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        template = await session.scalar(select(TaskTemplate).limit(1))
        service = TaskService(repo, settings)
        fields = dict(
            estimate=template.estimated_minutes,
            proposed_points=template.points,
            template_id=template.id,
            template_version=template.version,
        )
        ready = await service.create(owner, template.title, "retained-ready", **fields)
        pending = await service.create(
            owner, template.title, "retained-pending", target=TaskTarget.PEER, **fields
        )
        old_snapshot = dict(ready.template_snapshot)
        templates = TaskTemplateService(TaskTemplateRepository(session), settings)
        await templates.edit(peer, template.id, template.version, title="Changed", points=900)
        await templates.disable(owner, template.id, template.version)
        assert not template.is_active and template.version > ready.template_version
        assert (await repo.list(owner, scope="all"))[1] == 2
        assert (await repo.list(peer, pending=True))[1] == 1
        assert await repo.pending_next(peer) == (pending, 1)
        assert await NotificationRepository(session).pending_count(peer.id) == 1
        assert (await repo.summary(owner)).todo_points == old_snapshot["points"]
        assert (await repo.summary(peer)).pending_count == 1
        assert await service.view(owner, ready.id) is ready
        await service.rate(peer, pending.id, 700, pending.version)
        await service.change_status(owner, ready.id, "DONE", ready.version)
        assert ready.awarded_points == old_snapshot["points"]
        assert pending.status == "TODO" and pending.rated_points == 700
        assert ready.template_snapshot == pending.template_snapshot == old_snapshot
