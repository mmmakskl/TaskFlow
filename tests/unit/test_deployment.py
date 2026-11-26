from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError
from aiogram.methods import SetWebhook
from pydantic import ValidationError

from app.config.settings import Settings
from app.web.webhook import configure_webhook


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?x=1",
        "http://example.com",
    ],
)
def test_production_requires_safe_webhook_origin(settings, url):
    with pytest.raises(ValidationError):
        Settings(
            **{
                **settings.model_dump(),
                "environment": "production",
                "register_webhook": True,
                "webhook_base_url": url,
            },
            _env_file=None,
        )


def test_production_cannot_disable_webhook(settings):
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "environment": "production"}, _env_file=None)


async def test_webhook_retry_is_bounded_and_preserves_pending_updates(settings, monkeypatch):
    settings.webhook_base_url = "https://example.com/"
    bot = Mock(
        set_webhook=AsyncMock(
            side_effect=[
                TelegramNetworkError(SetWebhook(url="https://example.com"), "transient"),
                True,
            ]
        )
    )
    sleep = AsyncMock()
    monkeypatch.setattr("app.web.webhook.asyncio.sleep", sleep)
    await configure_webhook(bot, settings)
    assert bot.set_webhook.await_count == 2 and sleep.await_count == 1
    args = bot.set_webhook.call_args.kwargs
    assert args["url"] == "https://example.com/telegram/webhook"
    assert args["secret_token"] == settings.webhook_secret.get_secret_value()
    assert args["drop_pending_updates"] is False and args["max_connections"] == 5


@pytest.mark.parametrize("failure,count", [("network", 3), ("rate_limit", 1), ("unauthorized", 1)])
async def test_webhook_startup_fails_closed(settings, monkeypatch, failure, count):
    method = SetWebhook(url="https://example.com")
    error = {
        "network": TelegramNetworkError(method, "transient"),
        "rate_limit": TelegramRetryAfter(method, "wait", retry_after=60),
        "unauthorized": TelegramUnauthorizedError(method, "invalid"),
    }[failure]
    bot = Mock(set_webhook=AsyncMock(side_effect=error))
    monkeypatch.setattr("app.web.webhook.asyncio.sleep", AsyncMock())
    with pytest.raises(type(error)):
        await configure_webhook(bot, settings)
    assert bot.set_webhook.await_count == count


def test_startup_migrates_before_exec_and_passes_graceful_timeout(settings, monkeypatch):
    from app import startup

    order = Mock()
    monkeypatch.setattr(startup, "Settings", lambda: settings)
    monkeypatch.setattr(startup, "migrate", order.migrate)
    monkeypatch.setattr(startup.os, "execvp", order.exec)
    monkeypatch.setenv("PORT", "12345")
    startup.main()
    assert [call[0] for call in order.mock_calls] == ["migrate", "exec"]
    arguments = order.exec.call_args.args[1]
    assert "12345" in arguments and "--timeout-graceful-shutdown" in arguments


@pytest.mark.parametrize("failure", ["migration", "port"])
def test_startup_does_not_serve_after_failure(settings, monkeypatch, failure):
    from app import startup

    migrate, execute = Mock(), Mock()
    if failure == "migration":
        migrate.side_effect = RuntimeError("migration failed")
    monkeypatch.setattr(startup, "Settings", lambda: settings)
    monkeypatch.setattr(startup, "migrate", migrate)
    monkeypatch.setattr(startup.os, "execvp", execute)
    monkeypatch.setenv("PORT", "70000" if failure == "port" else "10000")
    with pytest.raises(SystemExit):
        startup.main()
    execute.assert_not_called()


def test_html_widget_flag_defaults_to_disabled(settings):
    data = settings.model_dump(exclude={"telegram_html_widget_enabled"})
    assert Settings(**data, _env_file=None).telegram_html_widget_enabled is False


@pytest.mark.parametrize("value", [True, "true", "1"])
def test_unimplemented_html_widget_cannot_be_enabled(settings, value):
    with pytest.raises(ValidationError, match="TELEGRAM_HTML_WIDGET_ENABLED"):
        Settings(**{**settings.model_dump(), "telegram_html_widget_enabled": value}, _env_file=None)


def test_html_widget_env_is_validated(settings, monkeypatch):
    data = settings.model_dump(exclude={"telegram_html_widget_enabled"})
    monkeypatch.setenv("TELEGRAM_HTML_WIDGET_ENABLED", "false")
    assert Settings(**data, _env_file=None).telegram_html_widget_enabled is False
    monkeypatch.setenv("TELEGRAM_HTML_WIDGET_ENABLED", "true")
    with pytest.raises(ValidationError, match="TELEGRAM_HTML_WIDGET_ENABLED"):
        Settings(**data, _env_file=None)
