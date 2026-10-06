"""Input and navigation guards; never decide business permissions or scoring."""

from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.bot.callbacks.actions import Action
from app.bot.states.forms import ConfirmationForm, PenaltyForm, RatingForm, SettingsForm, TaskForm
from app.schemas.penalties import PenaltyPreview

GROUPS = (TaskForm, RatingForm, PenaltyForm, SettingsForm, ConfirmationForm)
KNOWN_STATES = {state.state for group in GROUPS for state in group.__all_states__}
NAVIGATION = {
    "menu",
    "list",
    "task",
    "ratings",
    "results",
    "history",
    "settings",
    "types",
    "type_view",
    "penalty_list",
}
NEW_FORMS = {"new", "penalty", "type_new", "user_setting"}
FORM_ACTIONS = {
    "TaskForm": {
        "calendar",
        "deadline",
        "comment_add",
        "comment_skip",
        "task_back",
        "task_estimate",
        "task_title_keep",
        "task_save",
        "task_clear",
    },
    "PenaltyForm": {
        "penalty_target",
        "penalty_kinds",
        "penalty_type",
        "penalty_comment",
        "penalty_comment_skip",
        "penalty_comment_keep",
        "penalty_commit",
        "penalty_back",
        "penalty_value",
    },
    "SettingsForm": {
        "type_method",
        "settings_back",
        "setting_value",
        "type_rate_value",
        "type_skip",
        "type_keep",
        "type_clear",
    },
    "RatingForm": set(),
    "ConfirmationForm": {"status_confirm", "penalty_delete_confirm", "type_toggle_confirm"},
}
TEXT_STATES = {
    TaskForm.title.state,
    TaskForm.estimate.state,
    TaskForm.comment.state,
    TaskForm.comment_choice.state,
    TaskForm.edit.state,
    RatingForm.points.state,
    PenaltyForm.quantity.state,
    PenaltyForm.comment.state,
    PenaltyForm.comment_choice.state,
    SettingsForm.target.state,
    SettingsForm.type_name.state,
    SettingsForm.type_emoji.state,
    SettingsForm.type_description.state,
    SettingsForm.type_rate.state,
    SettingsForm.type_edit.state,
}
REQUIRED = {
    RatingForm.points.state: {"task_id", "task_version"},
    PenaltyForm.kind.state: {"target_id"},
    PenaltyForm.quantity.state: {"target_id", "type_id", "type_version", "calculation_type"},
    PenaltyForm.comment_choice.state: {"target_id", "preview", "calculation_type"},
    PenaltyForm.comment.state: {"target_id", "preview", "calculation_type"},
    PenaltyForm.confirm.state: {"target_id", "preview", "calculation_type"},
    SettingsForm.target.state: {"field"},
    SettingsForm.type_edit.state: {"type_id", "type_version", "field"},
    ConfirmationForm.action.state: {"confirmation"},
}


def draft_valid(current: str | None, data: dict[str, Any]) -> bool:
    if current is None:
        return True
    if current not in KNOWN_STATES or type(data.get("flow")) is not int or not data.get("key"):
        return False
    needed = REQUIRED.get(current, set())
    if current.startswith("TaskForm:") and current != TaskForm.title.state:
        needed = needed | ({"edit_id", "edit_version"} if data.get("edit_id") else {"title"})
    if current.startswith("SettingsForm:type_") and not data.get("type_id"):
        needed = needed | {
            SettingsForm.type_emoji.state: {"name"},
            SettingsForm.type_description.state: {"name", "emoji"},
            SettingsForm.type_method.state: {"name", "emoji", "description"},
            SettingsForm.type_rate.state: {"name", "emoji", "description", "calculation_type"},
        }.get(current, set())
    if not needed <= data.keys():
        return False
    if current in {
        PenaltyForm.comment_choice.state,
        PenaltyForm.comment.state,
        PenaltyForm.confirm.state,
    }:
        try:
            PenaltyPreview.model_validate(data.get("preview"))
        except ValidationError:
            return False
    if current in {SettingsForm.target.state, SettingsForm.type_edit.state} and not isinstance(
        data.get("field"), str
    ):
        return False
    if current == ConfirmationForm.action.state and not isinstance(data.get("confirmation"), dict):
        return False
    if data.get("deadline"):
        try:
            datetime.fromisoformat(data["deadline"])
        except (TypeError, ValueError):
            return False
    return True


async def input_warning(
    event: Message, state: FSMContext, known_command: bool = False
) -> str | None:
    current = await state.get_state()
    if event.text is None:
        return "Здесь нужен текст. Выбери кнопку или напиши сообщение."
    if event.text.lstrip().startswith("/") and not known_command:
        return "Используй кнопки ниже. Чтобы выйти из формы, нажми «Меню»."
    if current and current not in TEXT_STATES and not event.text.startswith("/"):
        return "На этом шаге выбери кнопку ниже. Черновик сохранён."
    return None


async def callback_warning(event: CallbackQuery, state: FSMContext) -> str | None:
    try:
        action = Action.unpack(event.data or "")
    except (ValueError, TypeError):
        return None  # The router fallback restores controls for malformed/obsolete data.
    current = await state.get_state()
    data = await state.get_data()
    if action.name in NAVIGATION or action.name in NEW_FORMS or action.name == "noop":
        return None
    message_id = getattr(event.message, "message_id", None)
    if data.get("message_id") and message_id != data["message_id"]:
        return "Эта кнопка из старого сообщения. Используй текущее меню."
    markup = data.get("screen_markup") or {}
    visible = {
        button.get("callback_data") for row in markup.get("inline_keyboard", []) for button in row
    }
    if event.data not in visible:
        return "Эта кнопка устарела. Используй актуальные кнопки меню."
    if current:
        allowed = FORM_ACTIONS.get(current.split(":")[0], set())
        if current.startswith("TaskForm:") and data.get("edit_id"):
            allowed = allowed | {"edit"}
        if action.name not in allowed:
            return "Сначала заверши форму или вернись в меню. Черновик сохранён."
    return None


async def adopt_entry_message(event: CallbackQuery, bot: Bot, state: FSMContext) -> bool:
    """Open notification/old-menu navigation where the user is currently looking."""
    try:
        action = Action.unpack(event.data or "")
    except (ValueError, TypeError):
        return False
    if action.name not in NAVIGATION | NEW_FORMS:
        return False
    message_id = getattr(event.message, "message_id", None)
    data = await state.get_data()
    previous = data.get("message_id")
    if not message_id or previous == message_id:
        return False
    if previous:
        try:
            await bot.edit_message_reply_markup(
                chat_id=event.from_user.id, message_id=previous, reply_markup=None
            )
        except (TelegramAPIError, TelegramNetworkError):
            pass  # Optional cleanup; old mutations remain protected by message ID/version checks.
    await state.update_data(
        message_id=message_id, rendered_text=None, screen_text=None, screen_markup=None
    )
    return True
