import io
import logging
import sys

import pytest
from sqlalchemy.exc import DBAPIError

from app.config.logging import configure_logging


def test_uvicorn_error_uses_secret_and_database_redaction(settings, monkeypatch):
    stream = io.StringIO()
    logger = logging.getLogger("uvicorn.error")
    monkeypatch.setattr(logger, "handlers", [logging.StreamHandler(stream)])
    monkeypatch.setattr(logger, "propagate", False)
    configure_logging(settings)
    # Capture whichever handler the production logging configuration selects.
    for handler in logger.handlers or logging.getLogger().handlers:
        monkeypatch.setattr(handler, "stream", stream)
    parameters = {"title": "PRIVATE_TITLE"}
    original = Exception("DETAIL PRIVATE_ROW")
    try:
        raise DBAPIError("INSERT", parameters, original)
    except DBAPIError:
        logger.error(
            "ASGI failure token=%s", settings.bot_token.get_secret_value(), exc_info=sys.exc_info()
        )
    output = stream.getvalue()
    assert "PRIVATE_ROW" not in output
    assert "PRIVATE_TITLE" not in output
    assert settings.bot_token.get_secret_value() not in output
    assert "database operation failed" in output


async def test_denied_sender_blocking_bot_does_not_poison_update(settings):
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage
    from aiogram.types import Chat, Message, User

    from app.bot.middlewares.access import AccessMiddleware

    event = AsyncMock(spec=Message)
    event.from_user = User(id=9000, is_bot=False, first_name="unknown")
    event.chat = Chat(id=9000, type="private")
    event.answer = AsyncMock(
        side_effect=TelegramForbiddenError(SendMessage(chat_id=9000, text="locked"), "blocked")
    )
    handler = AsyncMock()
    assert await AccessMiddleware(settings)(handler, event, {}) is None
    handler.assert_not_awaited()


def test_persisted_naive_deadline_is_not_a_resumable_draft():
    from app.bot.middlewares.interaction import draft_valid
    from app.bot.states.forms import TaskForm

    data = {"flow": 1, "key": "draft", "title": "Task", "deadline": "2026-10-08T23:59:59"}
    assert not draft_valid(TaskForm.deadline.state, data)


def test_persisted_oversized_flow_is_not_a_resumable_draft():
    from app.bot.middlewares.interaction import draft_valid
    from app.bot.states.forms import TaskForm

    assert not draft_valid(TaskForm.title.state, {"flow": 2**40, "key": "draft"})


async def test_maximum_unicode_task_screens_and_callbacks_fit_limits():
    from datetime import UTC, datetime
    from html.parser import HTMLParser
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models import Task, User
    from app.services.ui import UIService

    users = [
        User(id=i, telegram_id=1000 + i, display_name="😀" * 50, timezone="Europe/Helsinki")
        for i in (1, 2)
    ]
    task = Task(
        id=2147483647,
        version=2147483647,
        owner_user_id=1,
        evaluator_user_id=2,
        title="😀" * 200,
        comment="😀" * 1500,
        estimated_minutes=10080,
        status="DONE",
        rated_points=10000,
        awarded_points=10000,
        completed_at=datetime(2026, 10, 7, tzinfo=UTC),
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
    )
    repo = SimpleNamespace(
        users=AsyncMock(return_value=users), pending_next=AsyncMock(return_value=(task, 1))
    )
    tasks = SimpleNamespace(view=AsyncMock(return_value=task), repo=repo)
    ui = UIService(tasks, None, None)

    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.text = ""

        def handle_data(self, data):
            self.text += data

    for text, markup in (await ui.task_details(users[0], task.id), await ui.rating(users[1])):
        parser = VisibleText()
        parser.feed(text)
        assert len(parser.text.encode("utf-16-le")) // 2 <= 4096
        assert all(
            len(button.callback_data.encode()) <= 64
            for row in markup.inline_keyboard
            for button in row
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_uvicorn_access_log_preserves_startup_policy(settings, monkeypatch, enabled):
    logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(logger, "handlers", [logging.StreamHandler()] if enabled else [])
    monkeypatch.setattr(logger, "propagate", False)
    configure_logging(settings)
    assert logger.propagate is enabled
    assert logger.handlers == []
