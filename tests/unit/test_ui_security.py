from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from pydantic import ValidationError

from app.bot.callbacks.actions import Action, callback
from app.bot.keyboards.common import inline_calendar, pager
from app.bot.middlewares.access import AccessMiddleware
from app.bot.render import progress, require_current, safe
from app.config.logging import RedactingFormatter
from app.config.settings import Settings
from app.services.notifications import cooldown_ready
from app.services.rules import DomainError


def test_html_escaped() -> None:
    assert safe("<b>A&B</b>") == "&lt;b&gt;A&amp;B&lt;/b&gt;"


@pytest.mark.parametrize(
    "score,target,percent", [(-100, 10000, "0%"), (7350, 10000, "73%"), (20000, 10000, "100%")]
)
def test_progress(score: int, target: int, percent: str) -> None:
    assert progress(score, target).endswith(percent)


def test_pagination_and_callback_limits() -> None:
    assert len(pager("list", 0, False)) == 1
    assert len(pager("list", 1, True)) == 3
    packed = callback("penalty_commit", version=2147483647, value="x" * 32)
    assert len(packed.encode()) <= 64
    assert Action.unpack(packed).name == "penalty_commit"
    with pytest.raises((ValueError, ValidationError)):
        Action.unpack("a:status:invalid:bad:op")


def test_calendar_has_leap_day_and_monday_first() -> None:
    markup = inline_calendar(2028, 2, 123)
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.text == "29" and "2028-02-29" in (b.callback_data or "") for b in buttons)
    assert markup.inline_keyboard[1][0].text == "Пн"
    with pytest.raises(ValueError):
        inline_calendar(2026, 13)


def test_cooldown_exact_boundary() -> None:
    now = datetime.now(UTC)
    assert cooldown_ready(None, now, 60)
    assert not cooldown_ready(now - timedelta(minutes=59, seconds=59), now, 60)
    assert cooldown_ready(now - timedelta(minutes=60), now, 60)


async def test_stale_form_does_not_accept_different_flow() -> None:
    state = AsyncMock()
    state.get_state.return_value = "TaskForm:deadline"
    state.get_data.return_value = {"flow": 5}
    with pytest.raises(DomainError):
        await require_current(state, "TaskForm:deadline", 4)


async def test_unauthorized_user_never_calls_handler(settings: Settings) -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = User(id=9999, is_bot=False, first_name="Stranger")
    message.chat = Chat(id=9999, type="private")
    handler = AsyncMock()
    await AccessMiddleware(settings)(handler, message, {})
    handler.assert_not_awaited()
    assert "приватный" in message.answer.call_args.args[0]


def test_settings_reject_same_ids_sqlite_and_short_secret(settings: Settings) -> None:
    data = settings.model_dump()
    for change in (
        {"user_vika_id": 1001},
        {"database_url": "sqlite:///x"},
        {"webhook_secret": "short"},
        {"rating_notification_cooldown_minutes": 59},
    ):
        with pytest.raises(ValidationError):
            Settings(**{**data, **change}, _env_file=None)


def test_logging_redacts_secrets() -> None:
    import logging

    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "",
        1,
        "postgresql://user:pass@host/db token=123456789:abcdefghijklmnopqrstuv",
        (),
        None,
    )
    formatted = RedactingFormatter([]).format(record)
    assert "pass@host" not in formatted and "abcdefghijklmnopqrstuv" not in formatted


async def test_database_errors_hide_bound_private_parameters(settings: Settings) -> None:
    from app.db.session import database

    engine, _ = database(settings)
    assert engine.sync_engine.hide_parameters is True
    await engine.dispose()


def test_database_failure_detail_never_enters_logs() -> None:
    import logging
    import sys

    from sqlalchemy.exc import DBAPIError

    try:
        raise DBAPIError(
            "INSERT private",
            {"comment": "private-comment"},
            Exception("DETAIL: Failing row contains(private-title)"),
        )
    except DBAPIError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "update_failed update_id=5", (), sys.exc_info()
        )
        formatted = RedactingFormatter([]).format(record)
    assert "private-title" not in formatted and "private-comment" not in formatted
    assert "database operation failed" in formatted and "update_id=5" in formatted


async def test_penalty_date_uses_user_timezone() -> None:
    from types import SimpleNamespace

    from app.services.ui import UIService

    user = SimpleNamespace(id=1, display_name="Макс", timezone="Europe/Helsinki")
    penalty = SimpleNamespace(
        id=1,
        target_user_id=1,
        name_snapshot="Instagram",
        points=100,
        version=1,
        created_at=datetime(2026, 10, 6, 21, 30, tzinfo=UTC),
    )
    penalties = SimpleNamespace(
        list=AsyncMock(return_value=([penalty], False)),
        repo=SimpleNamespace(users=AsyncMock(return_value=[user])),
    )
    ui = UIService(None, penalties, None)
    text, _ = await ui.penalty_list(user, 0)
    assert "07.10.2026" in text
