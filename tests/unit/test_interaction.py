from datetime import date

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.callbacks.actions import callback
from app.bot.handlers.common import confirm_action, confirmed_action
from app.bot.keyboards.common import button, inline_calendar, keyboard
from app.bot.middlewares.interaction import callback_warning, draft_valid, input_warning
from app.bot.render import render, reset, short
from app.bot.states.forms import TaskForm
from app.services.rules import DomainError
from tests.fakes import TelegramSession


@pytest.fixture
async def state():
    storage = MemoryStorage()
    yield FSMContext(storage, StorageKey(bot_id=1, chat_id=1001, user_id=1001))
    await storage.close()


async def test_confirmation_nonce_expires_on_navigation_and_new_confirmation(state):
    first = await confirm_action(state, "status_confirm", 1, 2, target="TODO")
    assert (await confirmed_action(state, first))["target"] == "TODO"
    await reset(state)
    with pytest.raises(DomainError):
        await confirmed_action(state, first)
    second = await confirm_action(state, "status_confirm", 1, 2, target="TODO")
    assert first.value != second.value
    with pytest.raises(DomainError):
        await confirmed_action(state, first)
    assert await confirmed_action(state, second)


async def test_reset_keeps_screen_navigation_but_clears_draft(state):
    await state.set_state(TaskForm.title)
    await state.set_data(
        {
            "message_id": 10,
            "rendered_text": "cached",
            "filter": "done",
            "page": 2,
            "key": "draft",
            "title": "draft",
        }
    )
    await reset(state)
    assert await state.get_state() is None
    assert await state.get_data() == {
        "message_id": 10,
        "rendered_text": "cached",
        "filter": "done",
        "page": 2,
    }
    await reset(state, keep_navigation=False)
    assert "page" not in await state.get_data()


@pytest.mark.parametrize("text", [None, "/unknown"])
async def test_unexpected_input_retains_controls(state, text):
    event = Message(
        message_id=1,
        date=0,
        chat=Chat(id=1001, type="private"),
        from_user=User(id=1001, is_bot=False, first_name="test"),
        text=text,
    )
    await state.set_state(TaskForm.title)
    assert await input_warning(event, state)
    assert await state.get_state() == TaskForm.title.state


async def test_old_message_mutation_rejected_but_notification_navigation_allowed(state):
    await state.update_data(message_id=10)
    event = CallbackQuery(
        id="test",
        chat_instance="test",
        from_user=User(id=1001, is_bot=False, first_name="test"),
        data=callback("status", 1, 2, "DONE"),
        message=Message(message_id=9, date=0, chat=Chat(id=1001, type="private")),
    )
    assert await callback_warning(event, state)
    assert (
        await callback_warning(event.model_copy(update={"data": callback("task", 1)}), state)
        is None
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"flow": 1, "key": "key"},
        {"flow": 1, "key": "key", "title": "title", "deadline": "invalid"},
    ],
)
def test_corrupt_task_draft_rejected(data):
    assert not draft_valid(TaskForm.deadline.state, data)


def test_single_line_labels_and_calendar_bounds():
    assert short("One\n  two", 20) == "One two"
    assert short("x" * 80, 10) == "x" * 9 + "…"
    for year, month in ((2020, 1), (2100, 12)):
        markup = inline_calendar(year, month)
        buttons = [button for row in markup.inline_keyboard for button in row]
        assert all(
            "2019-" not in (button.callback_data or "")
            and "2101-" not in (button.callback_data or "")
            for button in buttons
        )
    markup = inline_calendar(2026, 10, today=date(2026, 10, 7))
    assert not any(
        "2026-10-01" in (button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    )


async def test_render_uses_markup_only_when_text_matches_and_ignores_no_change(state):
    api = TelegramSession()
    bot = Bot("123456789:TEST_ONLY_TOKEN", session=api)
    await render(bot, 1001, state, "Text", keyboard([button("A", "menu")]))
    await render(bot, 1001, state, "Text", keyboard([button("B", "menu")]))
    assert isinstance(api.calls[-1], EditMessageReplyMarkup)
    assert len([call for call in api.calls if isinstance(call, SendMessage)]) == 1
    original = api.make_request

    async def no_change(bot, method, timeout=None):  # noqa: ASYNC109 - Bot API signature
        raise TelegramBadRequest(method=method, message="message is not modified")

    api.make_request = no_change
    await render(bot, 1001, state, "Text", keyboard([button("B", "menu")]))
    assert (await state.get_data())["rendered_text"] == "Text"
    api.make_request = original
    await render(bot, 1001, state, "New text", keyboard([button("B", "menu")]))
    assert isinstance(api.calls[-1], EditMessageText)
    await bot.session.close()


def test_task_confirmation_names_task_and_uses_award_snapshot():
    from types import SimpleNamespace

    from app.services.ui import UIService

    task = SimpleNamespace(
        title="<Task>", status="DONE", rated_points=999, awarded_points=500, kind="PERSONAL"
    )
    text = UIService.status_confirmation(task, "TODO")
    assert "&lt;Task&gt;" in text and "500" in text and "999" not in text
    assert "исходной недели" in text


@pytest.mark.parametrize("status", ["TODO", "IN_PROGRESS"])
def test_rerate_notification_shows_original_new_price_and_preserves_status(status):
    from app.models import Notification
    from app.notifications.messages import notification_message

    row = Notification(
        kind="rated",
        payload={
            "task_id": 1,
            "title": "<task>",
            "evaluator": "<peer>",
            "points": 700,
            "old_points": 500,
            "status": status,
        },
    )
    text, markup = notification_message(row, 0)
    assert "500 → 700" in text and "&lt;task&gt;" in text and "&lt;peer&gt;" in text
    assert ("остаётся в работе" in text) == (status == "IN_PROGRESS")
    assert markup.inline_keyboard[0][0].callback_data.startswith("a:task:1:")


def test_rejection_notification_escapes_author_and_title():
    from app.models import Notification
    from app.notifications.messages import notification_message

    text, markup = notification_message(
        Notification(
            kind="task_rejected", payload={"task_id": 5, "title": "<task>", "actor": "<peer>"}
        ),
        0,
    )
    assert "отклонено" in text and "&lt;task&gt;" in text and "&lt;peer&gt;" in text
    assert markup.inline_keyboard[0][0].callback_data.startswith("a:task:5:")


def test_new_partial_target_and_template_form_validation():
    from app.bot.states.forms import TemplateForm

    assert draft_valid(TaskForm.target.state, {"flow": 1, "key": "key", "target": "SELF"})
    assert not draft_valid(TaskForm.target.state, {"flow": 1, "key": "key", "target": "OTHER"})
    assert not draft_valid(
        TemplateForm.field.state, {"flow": 1, "key": "key", "field": "estimated_minutes"}
    )
    assert draft_valid(
        TemplateForm.field.state,
        {"flow": 1, "key": "key", "field": "estimated_minutes", "title": "Template", "points": 400},
    )


@pytest.mark.parametrize(
    "current,fields",
    [
        ("TaskForm:draft", {"title": "Draft", "template_id": 1, "template_version": 1}),
        (
            "TaskForm:draft",
            {"title": "Draft", "template_id": 1, "template_version": 1, "template_snapshot": "bad"},
        ),
        (
            "TaskForm:draft",
            {
                "title": "Draft",
                "template_id": 1,
                "template_version": 1,
                "template_snapshot": {"title": "Snapshot", "proposed_points": 500},
            },
        ),
        (
            "TaskForm:draft",
            {
                "title": "Draft",
                "template_id": 1,
                "template_version": 1,
                "template_snapshot": {
                    "title": "Snapshot",
                    "proposed_points": True,
                    "estimated_minutes": None,
                },
            },
        ),
        ("TaskForm:points", {"title": "Draft", "save_template": True}),
        ("TaskForm:points", {"title": "Draft", "save_template": True, "template_save_key": 123}),
        ("TaskForm:draft", {"title": "Draft", "estimated_minutes": "60"}),
        ("TaskForm:draft", {"title": "Draft", "proposed_points": [500]}),
        ("RatingForm:points", {"task_id": 1, "task_version": 1, "rerate": True}),
        (
            "RatingForm:points",
            {"task_id": 1, "task_version": 1, "rerate": True, "old_points": "500"},
        ),
        ("RatingForm:points", {"task_id": True, "task_version": 1}),
        ("TemplateForm:field", {"field": ["title"]}),
        ("TemplateForm:field", {"field": "unknown"}),
        ("TemplateForm:field", {"field": "points", "title": 3}),
        ("TemplateForm:field", {"field": "title", "template_edit_id": 1}),
        ("TemplateForm:field", {"field": "estimated_minutes", "title": "Template", "points": True}),
        (
            "ConfirmationForm:action",
            {"confirmation": {"name": "rerate_confirm", "entity": 1, "version": 1}},
        ),
    ],
)
def test_collaborative_persisted_draft_missing_or_malformed_values_recover(current, fields):
    assert not draft_valid(current, {"flow": 1, "key": "draft-key", **fields})


@pytest.mark.parametrize(
    "current,fields",
    [
        ("TaskForm:title", {}),
        ("TaskForm:target", {"target": "SELF"}),
        (
            "TaskForm:draft",
            {
                "title": "Draft",
                "template_id": 1,
                "template_version": 1,
                "template_snapshot": {
                    "title": "Template",
                    "proposed_points": 500,
                    "estimated_minutes": None,
                },
            },
        ),
        (
            "TaskForm:points",
            {"title": "Draft", "save_template": True, "template_save_key": "save-key"},
        ),
        ("RatingForm:points", {"task_id": 1, "task_version": 1}),
        ("RatingForm:points", {"task_id": 1, "task_version": 1, "rerate": True, "old_points": 500}),
        ("TemplateForm:field", {"field": "title"}),
        ("TemplateForm:field", {"field": "points", "title": "Template"}),
        ("TemplateForm:field", {"field": "estimated_minutes", "title": "Template", "points": 500}),
        (
            "TemplateForm:field",
            {"field": "title", "template_edit_id": 1, "template_edit_version": 1},
        ),
    ],
)
def test_collaborative_valid_partial_drafts_remain_usable(current, fields):
    assert draft_valid(current, {"flow": 1, "key": "draft-key", **fields})
