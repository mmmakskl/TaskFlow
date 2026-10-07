import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import func, select, update

from app.db.session import database
from app.models import AuditEvent, Notification, Penalty, PenaltyType, ScoreEvent, User
from app.notifications.delivery import send_notification
from app.repositories.notifications import NotificationRepository
from app.repositories.penalties import PenaltyRepository
from app.repositories.tasks import TaskRepository
from app.services.notifications import NotificationService
from app.services.penalties import PenaltyService
from app.services.rules import StaleAction
from app.services.tasks import TaskService
from tests.fakes import TelegramSession
from tests.integration.test_business_migrations import alembic

pytestmark = pytest.mark.integration


@pytest.fixture
def telegram(settings):
    api = TelegramSession()
    return Bot(settings.bot_token.get_secret_value(), session=api), api


async def create_task(factory, settings, key):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, _ = await repo.users()
        task = await TaskService(repo, settings).create(owner, f"Task {key}", key)
        return task.id


async def due(factory):
    async with factory.begin() as session:
        repo = NotificationRepository(session)
        await session.execute(
            update(Notification)
            .where(Notification.status == "pending")
            .values(available_at=await repo.now() - timedelta(seconds=1))
        )


def sends(api):
    return [call for call in api.calls if isinstance(call, SendMessage)]


async def test_ten_quick_tasks_one_notification_with_live_count(factory, settings, telegram):
    bot, api = telegram
    for i in range(10):
        await create_task(factory, settings, f"batch-{i}")
    async with factory() as session:
        assert await session.scalar(select(func.count(Notification.id))) == 1
    await due(factory)
    assert (
        sum(await asyncio.gather(*(send_notification(factory, bot, settings) for _ in range(4))))
        == 1
    )
    assert len(sends(api)) == 1
    message = sends(api)[0]
    assert message.chat_id == settings.user_vika_id
    assert "Сейчас ждут твоей оценки: 10" in message.text
    assert message.reply_markup.inline_keyboard[0][0].text == "Оценить задачи"
    async with factory() as session:
        row = await session.scalar(select(Notification))
        assert row.status == "sent" and row.attempts == 1
        assert row.claimed_at and row.finished_at


async def test_debounce_is_first_event_bounded_and_survives_recreation(factory, settings, telegram):
    settings.rating_notification_debounce_seconds = 60
    await create_task(factory, settings, "first")
    async with factory() as session:
        original = (await session.scalar(select(Notification))).available_at
    await create_task(factory, settings, "second")
    assert not await send_notification(factory, telegram[0], settings)
    async with factory() as session:
        assert (await session.scalar(select(Notification))).available_at == original
    await due(factory)
    assert await send_notification(factory, telegram[0], settings)
    assert len(sends(telegram[1])) == 1


async def test_cooldown_survives_restart_and_delivers_deferred_batch(factory, settings, telegram):
    bot, api = telegram
    await create_task(factory, settings, "before-restart")
    await due(factory)
    assert await send_notification(factory, bot, settings)
    await create_task(factory, settings, "inside-cooldown")
    await due(factory)
    # New engine/pool, service and Bot object: no process-local cooldown state.
    engine, restarted = database(settings)
    try:
        restarted_bot = Bot(settings.bot_token.get_secret_value(), session=api)
        assert not await send_notification(restarted, restarted_bot, settings)
        assert len(sends(api)) == 1
        async with restarted.begin() as session:
            user = await session.scalar(select(User).where(User.role == "vika"))
            row = await session.scalar(select(Notification).where(Notification.status == "pending"))
            assert row.available_at == user.last_rating_notification_at + timedelta(minutes=60)
            user.last_rating_notification_at -= timedelta(minutes=60, seconds=1)
        await due(restarted)
        assert await send_notification(restarted, restarted_bot, settings)
        assert "оценки: 2" in sends(api)[-1].text
        assert not await send_notification(restarted, restarted_bot, settings)
        assert len(sends(api)) == 2
    finally:
        await engine.dispose()


async def test_concurrent_creations_and_duplicate_operation_coalesce(factory, settings, telegram):
    ids = await asyncio.gather(
        create_task(factory, settings, "race-a"),
        create_task(factory, settings, "race-b"),
        create_task(factory, settings, "race-a"),
    )
    assert ids[0] == ids[2]
    await due(factory)
    await asyncio.gather(*(send_notification(factory, telegram[0], settings) for _ in range(3)))
    assert len(sends(telegram[1])) == 1 and "оценки: 2" in sends(telegram[1])[0].text


async def test_claim_does_not_deadlock_against_task_foreign_key_locks(
    factory, settings, monkeypatch
):
    await create_task(factory, settings, "existing")
    await due(factory)
    inserted, release = asyncio.Event(), asyncio.Event()
    original = TaskRepository.enqueue_rating

    async def paused_enqueue(repo, *args):
        inserted.set()  # Task INSERT holds FK KEY SHARE locks until commit.
        await release.wait()
        await original(repo, *args)

    monkeypatch.setattr(TaskRepository, "enqueue_rating", paused_enqueue)
    creation = asyncio.create_task(create_task(factory, settings, "concurrent-insert"))
    try:
        await asyncio.wait_for(inserted.wait(), 5)
        async with factory.begin() as session:
            claimed = await asyncio.wait_for(
                NotificationService(NotificationRepository(session), settings).claim(), 5
            )
            assert claimed is not None
    finally:
        release.set()
        await asyncio.wait_for(creation, 5)
    async with factory.begin() as session:
        assert await NotificationService(NotificationRepository(session), settings).claim() is None


async def test_rated_before_delivery_skips_empty_queue_without_cooldown(
    factory, settings, telegram
):
    task_id = await create_task(factory, settings, "rated-before-send")
    async with factory.begin() as session:
        repo = TaskRepository(session)
        _, peer = await repo.users()
        await TaskService(repo, settings).rate(peer, task_id, 500, 1)
    await due(factory)
    for _ in range(3):
        await send_notification(factory, telegram[0], settings)
    assert len(sends(telegram[1])) == 1
    assert sends(telegram[1])[0].chat_id == settings.user_max_id
    async with factory() as session:
        assert (
            await session.scalar(select(User).where(User.role == "vika"))
        ).last_rating_notification_at is None


async def test_rating_owner_notification_once_and_no_status_notifications(
    factory, settings, telegram
):
    task_id = await create_task(factory, settings, "rate-once")

    async def rate():
        try:
            async with factory.begin() as session:
                repo = TaskRepository(session)
                _, peer = await repo.users()
                await TaskService(repo, settings).rate(peer, task_id, 750, 1)
            return True
        except StaleAction:
            return False

    assert sum(await asyncio.gather(rate(), rate())) == 1
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, _ = await repo.users()
        service = TaskService(repo, settings)
        task = await service.view(owner, task_id)
        await service.change_status(owner, task_id, "IN_PROGRESS", task.version)
        await service.change_status(owner, task_id, "DONE", task.version)
        await service.change_status(owner, task_id, "TODO", task.version, True)
        assert await session.scalar(select(func.count(Notification.id))) == 2
    await due(factory)
    for _ in range(3):
        await send_notification(factory, telegram[0], settings)
    messages = sends(telegram[1])
    assert len(messages) == 1 and "750 баллов" in messages[0].text
    assert messages[0].reply_markup.inline_keyboard[0][0].text == "Открыть задачу"


async def test_penalty_recipient_snapshot_notification_once(factory, settings, telegram):
    async def create():
        async with factory.begin() as session:
            repo = TaskRepository(session)
            target, actor = await repo.users()
            service = PenaltyService(repo, settings)
            kind = await session.scalar(
                select(PenaltyType).where(PenaltyType.seed_key == "instagram")
            )
            return (
                await service.create(
                    actor, target.id, await service.preview(kind.id, 5, None), "same-penalty", True
                )
            ).id

    ids = await asyncio.gather(create(), create())
    assert ids[0] == ids[1]
    async with factory.begin() as session:
        repo = TaskRepository(session)
        target, actor = await repo.users()
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        await PenaltyService(repo, settings).configure(
            actor, kind.id, kind.version, name="New", rate=150
        )
    await due(factory)
    await asyncio.gather(*(send_notification(factory, telegram[0], settings) for _ in range(2)))
    messages = sends(telegram[1])
    assert len(messages) == 1 and messages[0].chat_id == settings.user_max_id
    assert "Instagram" in messages[0].text and "−500" in messages[0].text
    assert "Вика" in messages[0].text and "New" not in messages[0].text


async def test_claim_rollback_does_not_consume_cooldown(factory, settings):
    await create_task(factory, settings, "rollback")
    await due(factory)
    with pytest.raises(RuntimeError):
        async with factory.begin() as session:
            assert await NotificationService(NotificationRepository(session), settings).claim()
            raise RuntimeError("transaction aborted")
    async with factory.begin() as session:
        assert await NotificationService(NotificationRepository(session), settings).claim()


async def test_interrupted_send_not_replayed_after_restart(factory, settings, telegram):
    await create_task(factory, settings, "interrupted")
    await due(factory)
    async with factory.begin() as session:
        claimed = await NotificationService(NotificationRepository(session), settings).claim()
        claimed[0].claimed_at -= timedelta(minutes=6)
    assert not await send_notification(factory, telegram[0], settings)
    assert sends(telegram[1]) == []
    async with factory() as session:
        row = await session.scalar(select(Notification))
        assert row.status == "uncertain" and row.error_code == "interrupted"
        assert row.attempts == 1


@pytest.mark.parametrize(
    "failure,status", [("network", "uncertain"), ("blocked", "failed"), ("unexpected", "uncertain")]
)
async def test_send_failure_not_retried_and_cooldown_kept(
    factory, settings, telegram, monkeypatch, failure, status
):
    await create_task(factory, settings, "send-failure")
    await due(factory)
    method = SendMessage(chat_id=settings.user_vika_id, text="test")
    error = {
        "network": TelegramNetworkError(method, "ambiguous"),
        "blocked": TelegramForbiddenError(method, "blocked"),
        "unexpected": RuntimeError("unclassified transport error"),
    }[failure]
    send = AsyncMock(side_effect=error)
    monkeypatch.setattr(telegram[0], "send_message", send)
    assert await send_notification(factory, telegram[0], settings)
    await create_task(factory, settings, "after-failure")
    await due(factory)
    assert not await send_notification(factory, telegram[0], settings)
    assert send.await_count == 1
    async with factory() as session:
        assert (
            await session.scalar(select(Notification).order_by(Notification.id))
        ).status == status


@pytest.mark.parametrize("retry_after", [30, 7200])
async def test_explicit_rate_limit_defers_rating_without_collision(
    factory, settings, telegram, monkeypatch, retry_after
):
    await create_task(factory, settings, "rate-limited")
    await due(factory)
    method = SendMessage(chat_id=settings.user_vika_id, text="test")

    async def rate_limited(*args, **kwargs):
        # A producer can queue another batch while this API request is in flight.
        await create_task(factory, settings, "during-send")
        raise TelegramRetryAfter(method, "wait", retry_after=retry_after)

    monkeypatch.setattr(telegram[0], "send_message", rate_limited)
    assert await send_notification(factory, telegram[0], settings)
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(Notification.id)).where(Notification.status == "pending")
            )
            == 1
        )
        row = await session.scalar(select(Notification).where(Notification.status == "pending"))
        assert row.available_at >= await NotificationRepository(session).now() + timedelta(
            seconds=retry_after - 5
        )
    await due(factory)
    assert not await send_notification(factory, telegram[0], settings)


async def test_unauthorized_recipient_and_unknown_kind_never_leak(factory, settings, telegram):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        user = (await repo.users())[0]
        session.add(
            Notification(
                recipient_user_id=user.id, kind="unknown", payload={}, dedupe_key="unknown"
            )
        )
    await due(factory)
    assert await send_notification(factory, telegram[0], settings)
    assert sends(telegram[1]) == []
    await create_task(factory, settings, "unauthorized")
    settings.user_vika_id = 9999
    await due(factory)
    assert not await send_notification(factory, telegram[0], settings)
    assert sends(telegram[1]) == []


async def test_notification_migration_roundtrip_preserves_cooldown_and_old_attempt(
    factory, settings
):
    await create_task(factory, settings, "migration")
    await due(factory)
    async with factory.begin() as session:
        row, user, _ = await NotificationService(NotificationRepository(session), settings).claim()
        last, row_id = user.last_rating_notification_at, row.id
    assert (await alembic(settings, "downgrade", "0003"))[0] == 0
    code, output = await alembic(settings, "upgrade", "head")
    assert code == 0, output

    async with factory() as session:
        row = await session.get(Notification, row_id)
        user = await session.get(User, row.recipient_user_id)
        assert user.last_rating_notification_at == last
        assert row.status == "uncertain" and row.error_code == "legacy_inflight"
    code, output = await alembic(settings, "check")
    assert code == 0, output


async def test_notification_worker_is_independent_of_slow_update_handler(
    factory, settings, telegram, monkeypatch
):
    from app.notifications.worker import run_workers

    await create_task(factory, settings, "independent")
    await due(factory)
    stop = asyncio.Event()
    original = telegram[0].send_message

    async def slow_update(*args):
        await stop.wait()
        return False

    async def send(*args, **kwargs):
        result = await original(*args, **kwargs)
        stop.set()
        return result

    monkeypatch.setattr("app.notifications.worker.process_update", slow_update)
    monkeypatch.setattr(telegram[0], "send_message", send)
    await asyncio.wait_for(run_workers(factory, None, telegram[0], settings, stop), 5)
    assert len(sends(telegram[1])) == 1


async def test_penalty_and_outbox_roll_back_together(factory, settings, monkeypatch):
    original = PenaltyRepository.add

    def fail_outbox(repo, row):
        if isinstance(row, Notification):
            raise RuntimeError("outbox unavailable")
        original(repo, row)

    monkeypatch.setattr(PenaltyRepository, "add", fail_outbox)
    async with factory.begin() as session:
        repo = TaskRepository(session)
        target, actor = await repo.users()
        service = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            await service.create(
                actor, target.id, await service.preview(kind.id, 5, None), "atomic", True
            )
        for model in (Penalty, ScoreEvent, AuditEvent, Notification):
            assert await session.scalar(select(func.count(model.id))) == 0


async def test_non_rating_explicit_rate_limit_retry_is_bounded(
    factory, settings, telegram, monkeypatch
):
    async with factory.begin() as session:
        user = (await TaskRepository(session).users())[0]
        session.add(
            Notification(
                recipient_user_id=user.id, kind="handler_error", payload={}, dedupe_key="retry"
            )
        )
    method = SendMessage(chat_id=settings.user_max_id, text="test")
    send = AsyncMock(side_effect=TelegramRetryAfter(method, "wait", retry_after=30))
    monkeypatch.setattr(telegram[0], "send_message", send)
    for attempt in range(3):
        await due(factory)
        assert await send_notification(factory, telegram[0], settings)
        assert not await send_notification(factory, telegram[0], settings)
        async with factory() as session:
            row = await session.scalar(select(Notification))
            assert row.attempts == attempt + 1
            assert row.status == ("pending" if attempt < 2 else "failed")
    assert send.await_count == 3
