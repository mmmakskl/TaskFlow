"""Independent release audit: hostile inputs and failures against real PostgreSQL."""

import asyncio

import httpx
import pytest
from aiogram import Bot
from sqlalchemy import func, select

from app.models import Task
from app.models.entities import Dialog, Inbox
from app.repositories.base import Repository
from app.repositories.tasks import TaskRepository
from app.services.rules import DomainError, StaleAction
from app.services.tasks import TaskService
from app.web.application import create_app
from tests.fakes import TelegramSession
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


async def test_unknown_users_do_not_allocate_persistent_fsm(factory, settings):
    driver = BotDriver(factory, settings)
    for user in range(9000, 9005):
        await driver.text(user, "/start")
        assert "приватный" in driver.screen(user).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Dialog.key))) == 0
        assert await session.scalar(select(func.count(Task.id))) == 0


@pytest.mark.parametrize("bad", ["nul\x00title", "surrogate\ud800"])
async def test_invalid_unicode_is_domain_error_without_poisoning_transaction(
    factory, settings, bad
):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        actor = (await repo.users())[0]
        service = TaskService(repo, settings)
        with pytest.raises(DomainError):
            await service.create(actor, bad, "invalid-unicode")
        assert await session.scalar(select(func.count(Task.id))) == 0
        task = await service.create(actor, "Still usable", "after-invalid-unicode")
        assert task.id > 0


@pytest.mark.parametrize("entity_id", [2**31, 2**100])
async def test_repository_rejects_unrepresentable_callback_ids(factory, entity_id):
    async with factory() as session:
        with pytest.raises(StaleAction):
            await Repository(session).get(Task, entity_id)
        actor = (await Repository(session).users())[0]
        with pytest.raises(DomainError):
            await TaskRepository(session).pending_next(actor, entity_id)


@pytest.mark.parametrize(
    "body",
    [
        b'{"update_id":9223372036854775808}',
        b'{"update_id":1,"message":{"message_id":1,"date":0,"chat":{"id":1001,"type":"private"},"text":"bad\\u0000"}}',
        b'{"update_id":1,"message":{"message_id":1,"date":0,"chat":{"id":1001,"type":"private"},"text":"bad\\ud800"}}',
        b'{"update_id":1,"extra":NaN}',
        b"[" * 1100 + b"0" + b"]" * 1100,
    ],
)
async def test_malformed_json_storage_values_rejected_before_database(factory, settings, body):
    bot = Bot(settings.bot_token.get_secret_value(), session=TelegramSession())
    app = create_app(settings, bot=bot, session_factory=factory, workers=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/telegram/webhook",
                content=body,
                headers={
                    "X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret.get_secret_value()
                },
            )
            assert response.status_code == 400
    async with factory() as session:
        assert await session.scalar(select(func.count(Inbox.update_id))) == 0


async def test_readiness_has_a_deadline_when_database_hangs(factory, settings, monkeypatch):
    bot = Bot(settings.bot_token.get_secret_value(), session=TelegramSession())
    app = create_app(settings, bot=bot, session_factory=factory, workers=False)
    async with app.router.lifespan_context(app):
        # Accelerate the application's own timeout, without timing out the HTTP client.
        original_timeout = asyncio.timeout
        monkeypatch.setattr(
            "app.web.application.asyncio.timeout", lambda seconds: original_timeout(0.02)
        )

        async def hang(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr("sqlalchemy.ext.asyncio.AsyncSession.execute", hang)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            response = await asyncio.wait_for(client.get("/ready"), timeout=0.3)
            assert response.status_code == 503


async def test_both_participants_auth_and_cross_chat_spoof_are_isolated(factory, settings):
    driver = BotDriver(factory, settings)
    for user in (1001, 1002):
        await driver.text(user, "/menu")
        assert "НЕДЕЛЯ" in driver.screen(user).text
    await driver.put(
        {
            "message": {
                "message_id": 88,
                "date": 0,
                "chat": {"id": -1234, "type": "group", "title": "Untrusted"},
                "from": {"id": 1001, "is_bot": False, "first_name": "Max"},
                "text": "/start",
            }
        }
    )
    assert "приватный" in driver.screen(-1234).text
    async with factory() as session:
        assert await session.scalar(select(func.count(Dialog.key))) == 2
        assert await session.scalar(select(func.count(Task.id))) == 0


async def test_deleted_task_during_custom_rating_keeps_score_zero(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Delete while rating")
    await driver.press(1001, "✅ Создать")
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    await driver.press(1002, "Другое")
    async with factory.begin() as session:
        task = await session.scalar(select(Task))
        from app.utils.time import utcnow

        task.deleted_at = utcnow()
    await driver.text(1002, "500")
    assert "устарела" in driver.screen(1002).text
    from app.models import ScoreEvent

    async with factory() as session:
        assert (await session.scalar(select(Task))).rated_points is None
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0


async def test_penalty_configuration_race_while_confirmation_is_open(factory, settings):
    from app.models import Penalty, PenaltyType, ScoreEvent
    from app.services.penalties import PenaltyService

    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Штраф")
    await driver.press(1001, "Макс")
    await driver.press(1001, "Instagram")
    await driver.press(1001, "15 мин")
    await driver.press(1001, "Пропустить")
    async with factory.begin() as session:
        repo = Repository(session)
        peer = (await repo.users())[1]
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        await PenaltyService(repo, settings).configure(peer, kind.id, kind.version, rate=150)
    await driver.press(1001, "Назначить")
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 0
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
    await driver.text(1001, "/menu")
    assert "НЕДЕЛЯ" in driver.screen(1001).text


async def test_network_failure_after_done_rolls_back_and_retry_awards_once(
    factory, settings, monkeypatch
):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import EditMessageText

    from app.models import AuditEvent, ScoreEvent
    from app.notifications.worker import process_update
    from app.utils.time import utcnow

    driver = BotDriver(factory, settings)
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Network transaction", "network-audit")
        await service.rate(peer, task.id, 700, task.version)
        task_id = task.id
    await driver.text(1001, "/menu")
    await driver.press(1001, "Все задачи")
    await driver.press(1001, "Network transaction")
    original = driver.api.make_request

    async def fail_edit(bot, method, timeout=None):  # noqa: ASYNC109
        if isinstance(method, EditMessageText) and "Начислено:" in method.text:
            raise TelegramNetworkError(method=method, message="injected transport timeout")
        return await original(bot, method, timeout)

    with monkeypatch.context() as patch:
        patch.setattr(driver.api, "make_request", fail_edit)
        with pytest.raises(AssertionError):  # BotDriver requires immediate processing success.
            await driver.press(1001, "Выполнено")
    async with factory.begin() as session:
        assert (await session.get(Task, task_id)).status == "TODO"
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        row = await session.get(Inbox, driver.next_update)
        assert row.status == "pending" and row.attempts == 1
        row.available_at = utcnow()
    assert await process_update(factory, driver.dp, driver.bot)
    assert not await process_update(factory, driver.dp, driver.bot)
    async with factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "DONE" and task.awarded_points == 700
        assert await session.scalar(select(func.sum(ScoreEvent.points))) == 700
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(AuditEvent.action == "status")
            )
            == 1
        )


@pytest.mark.parametrize(
    "created,completed,expected",
    [
        ("2026-09-28T10:00:00+00:00", "2026-10-04T21:00:00+00:00", 500),
        ("2026-10-05T10:00:00+00:00", "2026-10-11T20:59:59.999999+00:00", 500),
        ("2026-10-05T10:00:00+00:00", "2026-10-11T21:00:00+00:00", 0),
        ("2026-09-28T10:00:00+00:00", "2026-10-04T20:59:59.999999+00:00", 0),
    ],
)
async def test_completion_week_uses_independent_helsinki_boundary(
    factory, settings, monkeypatch, created, completed, expected
):
    from datetime import datetime

    from app.repositories.results import ResultRepository
    from app.services.results import ResultService

    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Week boundary", "independent-week")
        task.created_at = datetime.fromisoformat(created)
        await service.rate(peer, task.id, 500, task.version)
        monkeypatch.setattr("app.services.tasks.utcnow", lambda: datetime.fromisoformat(completed))
        await service.change_status(owner, task.id, "DONE", task.version)
    async with factory() as session:
        _, rows = await ResultService(ResultRepository(session), settings).week(
            at=datetime.fromisoformat("2026-10-07T12:00:00+00:00")
        )
        assert next(earned for user, earned, _ in rows if user.role == "max") == expected


async def test_ledger_reconciles_with_current_business_rows_after_mixed_actions(factory, settings):
    from app.models import Penalty, PenaltyType, ScoreEvent
    from app.services.penalties import PenaltyService

    async with factory.begin() as session:
        repo = TaskRepository(session)
        users = await repo.users()
        tasks = TaskService(repo, settings)
        penalties = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        for number in range(12):
            owner, peer = users[number % 2], users[(number + 1) % 2]
            task = await tasks.create(owner, f"Reconciliation {number}", f"reconcile-{number}")
            await tasks.rate(peer, task.id, (number + 1) * 250, task.version)
            if number % 2:
                await tasks.change_status(owner, task.id, "IN_PROGRESS", task.version)
            await tasks.change_status(owner, task.id, "DONE", task.version)
            if number % 3 == 0:
                await tasks.change_status(owner, task.id, "TODO", task.version, True)
                await tasks.change_status(owner, task.id, "DONE", task.version)
            if number % 4 == 0:
                await tasks.change_status(owner, task.id, "CANCELLED", task.version, True)
            penalty = await penalties.create(
                peer,
                owner.id,
                await penalties.preview(kind.id, number + 1, None),
                f"reconcile-penalty-{number}",
                True,
            )
            if number % 3 == 0:
                await penalties.delete(owner, penalty.id, penalty.version, True)
        for user in users:
            awarded = await session.scalar(
                select(func.coalesce(func.sum(Task.awarded_points), 0)).where(
                    Task.owner_user_id == user.id, Task.status == "DONE", Task.deleted_at.is_(None)
                )
            )
            lost = await session.scalar(
                select(func.coalesce(func.sum(Penalty.points), 0)).where(
                    Penalty.target_user_id == user.id, Penalty.deleted_at.is_(None)
                )
            )
            ledger = await session.scalar(
                select(func.sum(ScoreEvent.points)).where(ScoreEvent.user_id == user.id)
            )
            assert ledger == awarded - lost


async def test_cached_prelock_task_cannot_overwrite_new_rating(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, _ = await repo.users()
        task = await TaskService(repo, settings).create(
            owner, "Stale identity map", "identity-cache"
        )
        task_id = task.id
    async with factory.begin() as first:
        old_task = await first.get(Task, task_id)
        old_version = old_task.version
        actor = (await Repository(first).users())[0]
        async with factory.begin() as second:
            repo = TaskRepository(second)
            peer = (await repo.users())[1]
            await TaskService(repo, settings).rate(peer, task_id, 900, 1)
        with pytest.raises(StaleAction):
            await TaskService(TaskRepository(first), settings).change_status(
                actor, task_id, "CANCELLED", old_version, True
            )
    async with factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "TODO" and task.rated_points == 900
