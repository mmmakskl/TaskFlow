import asyncio

import httpx
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    SendMessage,
    SetChatMenuButton,
    SetMyCommands,
    SetWebhook,
)
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select

from app.bot.dispatcher import create_dispatcher
from app.config.settings import Settings
from app.models import Inbox, ScoreEvent, Task
from app.repositories.tasks import TaskRepository
from app.services.rules import DomainError
from app.services.tasks import TaskService
from app.web.application import create_app
from tests.fakes import TelegramSession
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


async def test_denied_reply_failure_cannot_delay_next_authorized_update(factory, settings):
    driver = BotDriver(factory, settings)
    original = driver.api.make_request

    async def send(bot, method, timeout=None):  # noqa: ASYNC109 - Bot API signature
        if isinstance(method, SendMessage) and method.chat_id == 9000:
            raise TelegramForbiddenError(method, "bot was blocked")
        return await original(bot, method, timeout)

    driver.api.make_request = send
    await driver.text(9000, "/start")
    await driver.text(1001, "/menu")
    assert "Неделя" in driver.screen(1001).text
    async with factory() as session:
        rows = list(await session.scalars(select(Inbox).order_by(Inbox.update_id)))
        assert len(rows) == 2
        assert all(row.status == "done" and row.attempts == 0 for row in rows)
    await driver.bot.session.close()


async def test_production_configuration_processes_real_worker_inbox(factory, settings):
    production = Settings(
        **{
            **settings.model_dump(),
            "environment": "production",
            "register_webhook": True,
            "webhook_base_url": "https://release.example.test",
        },
        _env_file=None,
    )
    api = TelegramSession()
    bot = Bot(production.bot_token.get_secret_value(), session=api)
    app = create_app(production, bot, create_dispatcher(production), factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            headers = {
                "X-Telegram-Bot-Api-Secret-Token": production.webhook_secret.get_secret_value()
            }
            payload = {
                "update_id": 120,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 1001, "type": "private"},
                    "from": {"id": 1001, "is_bot": False, "first_name": "Max"},
                    "text": "/menu",
                },
            }
            for _ in range(2):
                assert (
                    await client.post("/telegram/webhook", json=payload, headers=headers)
                ).status_code == 200
            async with asyncio.timeout(5):
                while True:
                    async with factory() as session:
                        row = await session.get(Inbox, 120)
                        if row.status == "done":
                            break
                    await asyncio.sleep(0.02)
            assert (await client.get("/ready")).status_code == 200
            registrations = [call for call in api.calls if isinstance(call, SetWebhook)]
            assert len(registrations) == 1
            assert registrations[0].url == "https://release.example.test/telegram/webhook"
            assert registrations[0].secret_token == production.webhook_secret.get_secret_value()
            menus = [
                call
                for call in api.calls
                if isinstance(call, SendMessage)
                and isinstance(call.reply_markup, InlineKeyboardMarkup)
            ]
            dashboards = [
                call for call in menus if isinstance(call.reply_markup, InlineKeyboardMarkup)
            ]
            app_buttons = [call for call in api.calls if isinstance(call, SetChatMenuButton)]
            assert {call.chat_id for call in app_buttons} == {None, 1001, 1002}
            assert all(call.menu_button.type == "commands" for call in app_buttons)
            assert len([call for call in api.calls if isinstance(call, SetMyCommands)]) == 3
            assert len(menus) == 1 and len(dashboards) == 1
            assert "Неделя" in dashboards[0].text
            assert all(call.chat_id == 1001 for call in menus)


async def test_concurrent_completion_and_reopen_cycles_match_database_ledger(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Repeat safely", "release-repeat")
        await service.rate(peer, task.id, 777, task.version)
        task_id, owner_telegram_id = task.id, owner.telegram_id

    async def mutate(status, version):
        try:
            async with factory.begin() as session:
                repo = TaskRepository(session)
                actor = await repo.user(owner_telegram_id)
                await TaskService(repo, settings).change_status(
                    actor, task_id, status, version, True
                )
            return True
        except DomainError:
            return False

    for _ in range(4):
        for status, expected in (("DONE", 777), ("TODO", 0)):
            async with factory() as session:
                version = (await session.get(Task, task_id)).version
            assert sum(await asyncio.gather(*(mutate(status, version) for _ in range(4)))) == 1
            async with factory() as session:
                total = await session.scalar(select(func.sum(ScoreEvent.points)))
                task = await session.get(Task, task_id)
                assert total == expected
                assert task.awarded_points == (777 if status == "DONE" else None)
                assert (task.completed_at is not None) == (status == "DONE")
