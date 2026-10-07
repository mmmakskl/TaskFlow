from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from httpx import ASGITransport, AsyncClient

from app.bot.dispatcher import create_dispatcher
from app.web.application import create_app
from tests.fakes import TelegramSession

pytestmark = pytest.mark.integration


async def test_failed_webhook_registration_closes_resources_and_never_ready(
    factory, settings, monkeypatch
):
    settings.register_webhook = True
    api = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=api)
    dp = create_dispatcher(settings)
    close_bot, close_storage = AsyncMock(), AsyncMock()
    monkeypatch.setattr(api, "close", close_bot)
    monkeypatch.setattr(dp.storage, "close", close_storage)
    monkeypatch.setattr(
        "app.web.application.configure_webhook",
        AsyncMock(side_effect=RuntimeError("registration failed")),
    )
    app = create_app(settings, bot, dp, factory, workers=False)
    with pytest.raises(RuntimeError, match="registration failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("Failed startup must never yield a ready application")
    assert app.state.ready is False
    close_bot.assert_awaited_once()
    close_storage.assert_awaited_once()


async def test_worker_exit_disables_readiness_and_webhook_but_health_stays_alive(
    factory, settings, monkeypatch
):
    monkeypatch.setattr("app.web.application.run_workers", AsyncMock())
    bot = Bot(settings.bot_token.get_secret_value(), session=TelegramSession())
    app = create_app(settings, bot=bot, session_factory=factory)
    async with app.router.lifespan_context(app):
        await app.state.worker_task
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 503
            headers = {
                "X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret.get_secret_value()
            }
            assert (
                await client.post("/telegram/webhook", json={"update_id": 99}, headers=headers)
            ).status_code == 503


async def test_non_ascii_secret_header_rejected_without_crash(factory, settings):
    bot = Bot(settings.bot_token.get_secret_value(), session=TelegramSession())
    app = create_app(settings, bot=bot, session_factory=factory, workers=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post(
                "/telegram/webhook",
                json={"update_id": 99},
                headers=[(b"x-telegram-bot-api-secret-token", b"\xff")],
            )
            assert result.status_code == 403
