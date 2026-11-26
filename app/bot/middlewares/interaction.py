"""Input and navigation guards; never decide business permissions or scoring."""

from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.bot.callbacks.actions import Action
from app.bot.states.forms import (
    ConfirmationForm,
    PenaltyForm,
    RatingForm,
    SettingsForm,
    TaskForm,
    TemplateForm,
)
from app.schemas.penalties import PenaltyPreview

GROUPS = (TaskForm, RatingForm, PenaltyForm, SettingsForm, ConfirmationForm, TemplateForm)
KNOWN_STATES = {state.state for group in GROUPS for state in group.__all_states__}
NAVIGATION = {
    "menu",
    "templates",
    "tpl_view",
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
        "draft_field",
        "draft_target",
        "draft_back",
        "draft_template",
        "draft_points_none",
        "calendar",
        "deadline",
        "comment_add",
        "comment_skip",
        "task_back",
        "task_estimate",
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
    "TemplateForm": {"tpl_none", "tpl_back", "tpl_mode"},
    "ConfirmationForm": {
        "status_confirm",
        "penalty_delete_confirm",
        "type_toggle_confirm",
        "rerate_confirm",
        "tpl_disable_confirm",
        "tpl_delete_confirm",
        "reject_confirm",
    },
}
TEXT_STATES = {
    TaskForm.title.state,
    TaskForm.points.state,
    TemplateForm.field.state,
    TaskForm.estimate.state,
    TaskForm.quantity.state,
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
    TemplateForm.field.state: {"field"},
    RatingForm.points.state: {"task_id", "task_version"},
    PenaltyForm.kind.state: {"target_id"},
    PenaltyForm.quantity.state: {"target_id", "type_id", "type_version", "calculation_type"},
    PenaltyForm.comment_choice.state: {"target_id", "preview", "calculation_type"},
    PenaltyForm.comment.state: {"target_id", "preview", "calculation_type"},
    PenaltyForm.confirm.state: {"target_id", "preview", "calculation_type"},
    SettingsForm.target.state: {"field"},
    SettingsForm.type_edit.state: {"type_id", "type_version", "field"},
    TaskForm.quantity.state: {"template_id", "template_version", "template_snapshot"},
    ConfirmationForm.action.state: {"confirmation"},
}


def valid_number(value: object, maximum: int = 2147483647) -> bool:
    return type(value) is int and 1 <= value <= maximum


def valid_text(value: object, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def new_fields_valid(current: str, data: dict[str, Any]) -> bool:
    """Validate persisted values indexed by collaborative-task form handlers."""
    if current.startswith("TaskForm:"):
        if "title" in data and not valid_text(data["title"], 200):
            return False
        for field, maximum in (
            ("proposed_points", 10000),
            ("estimated_minutes", 10080),
            ("template_quantity", 10080),
        ):
            if data.get(field) is not None and not valid_number(data[field], maximum):
                return False
        if "peer_name" in data and not isinstance(data["peer_name"], str):
            return False
        if data.get("template_id") is not None:
            snapshot = data.get("template_snapshot")
            if (
                not valid_number(data["template_id"])
                or not valid_number(data.get("template_version"))
                or not isinstance(snapshot, dict)
                or not {"title", "proposed_points", "estimated_minutes"} <= snapshot.keys()
            ):
                return False
            if (
                not valid_text(snapshot["title"], 200)
                or not valid_number(snapshot["proposed_points"], 10000)
                or (
                    snapshot["estimated_minutes"] is not None
                    and not valid_number(snapshot["estimated_minutes"], 10080)
                )
            ):
                return False
            if "pricing_mode" in snapshot:
                mode = snapshot.get("pricing_mode")
                if mode not in ("FIXED", "PER_TIME", "PER_QUANTITY"):
                    return False
                if mode == "FIXED":
                    if any(
                        snapshot.get(key) is not None for key in ("base_quantity", "unit_label")
                    ):
                        return False
                else:
                    maximum = 10080 if mode == "PER_TIME" else 10000
                    if not valid_number(snapshot.get("base_quantity"), maximum):
                        return False
                    if mode == "PER_TIME" and snapshot.get("unit_label") is not None:
                        return False
                    if mode == "PER_QUANTITY" and not valid_text(snapshot.get("unit_label"), 40):
                        return False
            if data.get("template_quantity") is not None:
                mode = snapshot.get("pricing_mode")
                maximum = 10080 if mode == "PER_TIME" else 10000
                if mode not in ("PER_TIME", "PER_QUANTITY") or not valid_number(
                    data["template_quantity"], maximum
                ):
                    return False
        if "save_template" in data and type(data["save_template"]) is not bool:
            return False
        if data.get("save_template") and not valid_text(data.get("template_save_key"), 80):
            return False
    if current == RatingForm.points.state:
        if not valid_number(data.get("task_id")) or not valid_number(data.get("task_version")):
            return False
        if "rerate" in data and type(data["rerate"]) is not bool:
            return False
        if data.get("rerate") and not valid_number(data.get("old_points"), 10000):
            return False
    if current == TemplateForm.field.state:
        if data.get("template_edit_id") is not None:
            if not valid_number(data["template_edit_id"]) or not valid_number(
                data.get("template_edit_version")
            ):
                return False
        else:
            if data.get("field") in (
                "points",
                "estimated_minutes",
                "pricing_mode",
                "base_quantity",
                "unit_label",
            ) and not valid_text(data.get("title"), 200):
                return False
            if data.get("field") in (
                "estimated_minutes",
                "pricing_mode",
                "base_quantity",
                "unit_label",
            ) and not valid_number(data.get("points"), 10000):
                return False
    if current == TemplateForm.field.state and data.get("field") in ("base_quantity", "unit_label"):
        mode = data.get("pricing_mode")
        if mode not in ("PER_TIME", "PER_QUANTITY"):
            return False
        if data.get("field") == "unit_label" and (
            mode != "PER_QUANTITY"
            or (data.get("pricing_edit") and not valid_number(data.get("base_quantity"), 10000))
        ):
            return False
    if current == ConfirmationForm.action.state:
        confirmation = data.get("confirmation")
        if isinstance(confirmation, dict) and confirmation.get("name") == "rerate_confirm":
            if not valid_number(confirmation.get("points"), 10000):
                return False
    return True


def draft_valid(current: str | None, data: dict[str, Any]) -> bool:
    if current is None:
        return True
    flow = data.get("flow")
    if (
        current not in KNOWN_STATES
        or type(flow) is not int
        or not 1 <= flow <= 2147483647
        or not valid_text(data.get("key"), 80)
    ):
        return False
    if not new_fields_valid(current, data):
        return False
    needed = set(REQUIRED.get(current, set()))
    if current.startswith("TaskForm:") and current not in (
        TaskForm.title.state,
        TaskForm.target.state,
    ):
        needed = needed | ({"edit_id", "edit_version"} if data.get("edit_id") else {"title"})
    if current.startswith("TaskForm:") and data.get("target", "SELF") not in (
        "SELF",
        "PEER",
        "SHARED",
    ):
        return False
    if current == TemplateForm.field.state:
        field = data.get("field")
        if field not in (
            "title",
            "points",
            "estimated_minutes",
            "pricing_mode",
            "base_quantity",
            "unit_label",
        ):
            return False
        if data.get("template_edit_id"):
            needed |= {"template_edit_id", "template_edit_version"}
        else:
            needed |= {"points": {"title"}, "estimated_minutes": {"title", "points"}}.get(
                field, set()
            )
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
            deadline = datetime.fromisoformat(data["deadline"])
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                return False
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
