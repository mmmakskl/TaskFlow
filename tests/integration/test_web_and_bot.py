from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.bot.dispatcher import create_dispatcher
from app.config.settings import Settings
from app.models import Inbox, Notification, Penalty, ScoreEvent, Task, User
from app.notifications.worker import process_update
from app.web.application import create_app
from tests.fakes import TelegramSession

pytestmark = pytest.mark.integration


async def test_webhook_secret_validation_health_and_dedup(factory, settings: Settings) -> None:
    api = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=api)
    app = create_app(settings, bot=bot, session_factory=factory, workers=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 503
        assert (await client.post("/telegram/webhook", json={"update_id": 1})).status_code == 403
        async with app.router.lifespan_context(app):
            assert (await client.get("/ready")).status_code == 200
            headers = {
                "X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret.get_secret_value()
            }
            for _ in range(2):
                assert (
                    await client.post("/telegram/webhook", json={"update_id": 1}, headers=headers)
                ).status_code == 200
            assert (
                await client.post("/telegram/webhook", json={"bad": True}, headers=headers)
            ).status_code == 400
            assert (
                await client.post("/telegram/webhook", content="x" * 65537, headers=headers)
            ).status_code == 413
            async with factory() as session:
                assert await session.scalar(select(func.count(Inbox.update_id))) == 1
    assert app.state.ready is False


class BotDriver:
    def __init__(self, factory, settings: Settings) -> None:
        self.factory = factory
        self.api = TelegramSession()
        self.bot = Bot(settings.bot_token.get_secret_value(), session=self.api)
        self.dp = create_dispatcher(settings)
        self.next_update = 100

    def screen(self, user_id: int):
        return next(
            call
            for call in reversed(self.api.calls)
            if isinstance(call, (SendMessage, EditMessageText)) and int(call.chat_id) == user_id
        )

    async def put(self, payload: dict) -> None:
        self.next_update += 1
        async with self.factory.begin() as session:
            session.add(
                Inbox(
                    update_id=self.next_update, payload={"update_id": self.next_update, **payload}
                )
            )
        assert await process_update(self.factory, self.dp, self.bot)

    async def text(self, user_id: int, text: str) -> None:
        await self.put(
            {
                "message": {
                    "message_id": self.next_update,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": user_id, "type": "private"},
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "text": text,
                }
            }
        )

    async def press(self, user_id: int, label: str | None = None, data: str | None = None) -> str:
        screen = self.screen(user_id)
        if data is None:
            candidates = [
                button
                for row in screen.reply_markup.inline_keyboard
                for button in row
                if label in button.text
            ]
            assert candidates, (label, screen.text)
            exact = [button for button in candidates if button.text == label]
            data = (exact or candidates)[0].callback_data
        await self.put(
            {
                "callback_query": {
                    "id": str(self.next_update),
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "chat_instance": "test",
                    "data": data,
                    "message": {
                        "message_id": getattr(screen, "message_id", None) or self.api.next_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": user_id, "type": "private"},
                        "from": {"id": self.bot.id, "is_bot": True, "first_name": "Bot"},
                        "text": screen.text,
                    },
                }
            }
        )
        return data


@pytest.mark.parametrize("expired_ack", [False, True])
async def test_real_aiogram_task_flow_with_mocked_bot_api(
    factory, settings: Settings, expired_ack: bool
) -> None:
    driver = BotDriver(factory, settings)
    driver.api.expired_ack = expired_ack
    await driver.text(1001, "/start")
    await driver.press(1001, "➕ Задача")
    await driver.text(1001, "Купить <билеты>")
    await driver.press(1001, "Без дедлайна")
    await driver.press(1001, "Пропустить")
    assert "&lt;билеты&gt;" in driver.screen(1001).text
    await driver.text(1002, "/start")
    await driver.press(1002, "Оценить")
    rating_callback = await driver.press(1002, "500")
    await driver.press(1002, data=rating_callback)
    await driver.text(1001, "/menu")
    await driver.press(1001, "📋 Задачи")
    await driver.press(1001, "Купить")
    done_callback = await driver.press(1001, "Выполнено")
    await driver.press(1001, data=done_callback)
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.status == "DONE" and task.awarded_points == 500
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 1
    sends = [call for call in driver.api.calls if isinstance(call, SendMessage)]
    assert len(sends) == 2  # One UI message per authorized user.
    await driver.bot.session.close()


@pytest.mark.parametrize("expired_ack", [False, True])
async def test_penalty_ui_confirmation_and_duplicate_callback(
    factory, settings: Settings, expired_ack: bool
) -> None:
    driver = BotDriver(factory, settings)
    driver.api.expired_ack = expired_ack
    await driver.text(1002, "/start")
    await driver.press(1002, "⚠️ Штраф")
    await driver.press(1002, "Макс")
    await driver.press(1002, "Instagram")
    await driver.text(1002, "12")
    await driver.press(1002, "Пропустить")
    async with factory() as session:
        assert await session.scalar(select(func.count(Penalty.id))) == 0
    callback_data = await driver.press(1002, "Назначить")
    await driver.press(1002, data=callback_data)
    async with factory() as session:
        penalty = await session.scalar(select(Penalty))
        assert penalty.points == 1200 and penalty.rate_snapshot == 100
        assert await session.scalar(select(func.count(Penalty.id))) == 1
    await driver.bot.session.close()


async def test_unauthorized_telegram_user_denied_without_creating_user(
    factory, settings: Settings
) -> None:
    driver = BotDriver(factory, settings)
    await driver.text(9999, "/start")
    assert "приватный" in driver.screen(9999).text
    async with factory() as session:
        assert await session.scalar(select(func.count(User.id))) == 2
        assert await session.scalar(select(func.count(Task.id))) == 0
    await driver.bot.session.close()


async def test_stale_penalty_form_cannot_select_new_draft(factory, settings: Settings) -> None:
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "⚠️ Штраф")
    await driver.press(1001, "Макс")
    old = next(
        b.callback_data
        for row in driver.screen(1001).reply_markup.inline_keyboard
        for b in row
        if "Instagram" in b.text
    )
    await driver.press(1001, "Отмена")
    await driver.press(1001, "⚠️ Штраф")
    await driver.press(1001, "Вика")
    await driver.press(1001, data=old)
    assert "Выбери тип" in driver.screen(1001).text
    await driver.press(1001, "Instagram")
    assert "количество минут" in driver.screen(1001).text
    await driver.bot.session.close()


async def test_live_penalty_configuration_and_personal_settings(
    factory, settings: Settings
) -> None:
    from app.models import PenaltyType

    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Типы штрафов")
    await driver.press(1001, "Instagram")
    await driver.press(1001, "Ставка")
    await driver.text(1001, "150")
    assert "150" in driver.screen(1001).text
    await driver.press(1001, "Выключить")
    await driver.press(1001, "Подтвердить")
    assert "Выключен" in driver.screen(1001).text
    async with factory() as session:
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.name == "Instagram"))
        assert kind.rate == 150 and not kind.is_active
    await driver.bot.session.close()


async def test_calendar_comment_edit_and_custom_rating_reopen(factory, settings: Settings) -> None:
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "➕ Задача")
    await driver.text(1001, "Проверить календарь")
    await driver.press(1001, "Выбрать дату")
    await driver.press(1001, "Без дедлайна")
    await driver.press(1001, "Добавить комментарий")
    await driver.text(1001, "<Комментарий>")
    await driver.text(1002, "/start")
    await driver.press(1002, "Оценить")
    await driver.press(1002, "Другое")
    await driver.text(1002, "800")
    await driver.press(1001, "К задачам")
    await driver.press(1001, "Проверить")
    assert "&lt;Комментарий&gt;" in driver.screen(1001).text
    await driver.press(1001, "Начать")
    await driver.press(1001, "Выполнено")
    await driver.press(1001, "↩️ К выполнению")
    await driver.press(1001, "Подтвердить")
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.status == "TODO" and task.awarded_points is None
        assert await session.scalar(select(func.sum(ScoreEvent.points))) == 0
    await driver.bot.session.close()


async def test_uvicorn_process_restart_preserves_database(factory, settings: Settings) -> None:
    import asyncio
    import os
    import sys

    environment = {
        **os.environ,
        "BOT_TOKEN": settings.bot_token.get_secret_value(),
        "WEBHOOK_SECRET": settings.webhook_secret.get_secret_value(),
        "DATABASE_URL": settings.database_url.get_secret_value(),
        "USER_MAX_ID": "1001",
        "USER_VIKA_ID": "1002",
        "REGISTER_WEBHOOK": "false",
        "ENVIRONMENT": "test",
    }
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "➕ Задача")
    await driver.text(1001, "Persistent task")
    await driver.press(1001, "Без дедлайна")
    await driver.press(1001, "Пропустить")
    async with factory.begin() as session:
        await session.execute(update(Notification).values(status="skipped"))
    for _ in range(2):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "19999",
            "--no-access-log",
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with AsyncClient(base_url="http://127.0.0.1:19999", timeout=1) as client:
                for _attempt in range(60):
                    try:
                        response = await client.get("/ready")
                        if response.status_code == 200:
                            break
                    except Exception:
                        pass
                    assert process.returncode is None
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("Uvicorn never became ready")
                assert (await client.get("/health")).status_code == 200
                assert (
                    await client.post("/telegram/webhook", json={"update_id": 1})
                ).status_code == 403
            async with factory() as session:
                assert await session.scalar(select(func.count(Task.id))) == 1
                assert (await session.scalar(select(Task))).title == "Persistent task"
        finally:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=15)
        assert process.returncode in (0, -15)  # Uvicorn re-raises SIGTERM after graceful shutdown.
    await driver.bot.session.close()


async def test_create_manual_penalty_type_and_use_without_restart(
    factory, settings: Settings
) -> None:
    from app.models import PenaltyType

    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Типы штрафов")
    await driver.press(1001, "Новый тип")
    await driver.text(1001, "Новый ручной")
    await driver.text(1001, "📝")
    await driver.text(1001, "Описание")
    await driver.press(1001, "Вручную")
    assert "Новый ручной" in driver.screen(1001).text
    async with factory() as session:
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.name == "Новый ручной"))
        assert kind is not None and kind.calculation_type == "MANUAL" and kind.rate == 0
    await driver.text(1002, "/menu")
    await driver.press(1002, "⚠️ Штраф")
    await driver.press(1002, "Макс")
    await driver.press(1002, "▶️")
    await driver.press(1002, "Новый ручной")
    await driver.text(1002, "123")
    await driver.press(1002, "Пропустить")
    await driver.press(1002, "Назначить")
    async with factory() as session:
        penalty = await session.scalar(select(Penalty))
        assert penalty.points == 123 and penalty.calculation_type_snapshot == "MANUAL"
    await driver.bot.session.close()


async def test_unexpected_handler_error_retries_and_notifies_once(
    factory, settings: Settings
) -> None:
    from unittest.mock import AsyncMock

    driver = BotDriver(factory, settings)
    await driver.text(1001, "/start")
    payload = {
        "update_id": 999,
        "message": {
            "message_id": 9,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 1001, "type": "private"},
            "from": {"id": 1001, "is_bot": False, "first_name": "Test"},
            "text": "/menu",
        },
    }
    async with factory.begin() as session:
        session.add(Inbox(update_id=999, payload=payload))
    driver.dp.feed_update = AsyncMock(side_effect=RuntimeError("injected handler failure"))
    for attempt in (1, 2):
        assert await process_update(factory, driver.dp, driver.bot) is False
        async with factory.begin() as session:
            record = await session.get(Inbox, 999)
            assert record.attempts == attempt and record.status == "pending"
            assert (
                await session.scalar(
                    select(func.count(Notification.id)).where(Notification.kind == "handler_error")
                )
                == 1
            )
            record.available_at = datetime.now(UTC)
    from app.notifications.worker import send_notification

    assert await send_notification(factory, driver.bot, settings)
    assert "Не получилось выполнить" in driver.screen(1001).text
    await driver.bot.session.close()
