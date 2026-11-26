"""Permanent private-chat navigation and durable form interruption."""

import logging
from secrets import randbelow
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from pydantic import ValidationError

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.keyboards.common import button, keyboard
from app.bot.middlewares.interaction import FORM_ACTIONS, NAVIGATION, NEW_FORMS, draft_valid
from app.bot.render import discard_input, render, reset
from app.config.settings import Settings
from app.models import User
from app.services.rules import DomainError
from app.services.ui import UIService

QUICK_KEYS = {
    "🏠 Меню": "menu",
    "➕ Задача": "new",
    "📋 Задачи": "tasks",
    "📥 Входящие": "inbox",
    "⚡ Шаблоны": "templates",
    "🏆 Результаты": "results",
}
COMMANDS = {
    "start": "Открыть TaskFlow",
    "menu": "Меню",
    "new": "Новая задача",
    "tasks": "Все задачи",
    "inbox": "Входящие предложения",
    "templates": "Шаблоны задач",
    "results": "Результаты недели",
    "cancel": "Отменить форму",
}
DESTINATIONS = {
    "start": Action(name="menu"),
    "menu": Action(name="menu"),
    "cancel": Action(name="menu"),
    "new": Action(name="new"),
    "tasks": Action(name="list", value="mine_all"),
    "inbox": Action(name="ratings"),
    "templates": Action(name="templates"),
    "results": Action(name="results"),
}


def fresh_nonce(previous: int | None = None) -> int:
    nonce = randbelow(2147483647) + 1
    return nonce if nonce != previous else nonce % 2147483647 + 1


async def register_commands(bot: Bot, chat_ids: tuple[int, ...] | list[int] | set[int]) -> None:
    for chat_id in chat_ids:
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat_id))
        except (TelegramAPIError, TelegramNetworkError):
            # Registration is a convenience; do not log Telegram errors containing tokens.
            logging.getLogger(__name__).warning("command_registration_failed")


async def message_destination(message: Message, bot: Bot) -> str | None:
    text = message.text or ""
    if text in QUICK_KEYS:
        return QUICK_KEYS[text]
    command = text.split(maxsplit=1)[0] if text else ""
    if not command.startswith("/"):
        return None
    command, _, mention = command[1:].partition("@")
    if mention:
        identity = await bot.me()
        if mention.lower() != (identity.username or "").lower():
            return None
    return command if command in COMMANDS else None


async def launch_miniapp(bot: Bot, actor: User, settings: Settings) -> None:
    """Clear a previously installed reply keyboard and show a one-time app launch CTA."""
    await bot.send_message(
        actor.telegram_id,
        "TaskFlow теперь открывается в приложении. Старые кнопки удалены.",
        reply_markup=ReplyKeyboardRemove(),
    )
    if not settings.webhook_base_url:
        await bot.send_message(
            actor.telegram_id,
            "Для локального запуска задай WEBHOOK_BASE_URL с HTTPS адресом Mini App.",
        )
        return
    url = f"{settings.webhook_base_url.rstrip('/')}/miniapp"
    await bot.send_message(
        actor.telegram_id,
        "Задачи, согласования и звёзды — в одном месте.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть TaskFlow", web_app=WebAppInfo(url=url))]
            ]
        ),
    )


def pending_valid(current: str | None, pending: Any) -> bool:
    if not isinstance(pending, dict):
        return False
    try:
        nonce = pending["nonce"]
        original = pending["data"]
        markup = InlineKeyboardMarkup.model_validate(original["screen_markup"])
        Action.model_validate(pending["destination"])
        return (
            current is not None
            and type(nonce) is int
            and 1 <= nonce <= 2147483647
            and isinstance(original, dict)
            and "nav_pending" not in original
            and bool(markup.inline_keyboard)
            and isinstance(original.get("screen_text"), str)
            and draft_valid(current, original)
        )
    except (KeyError, TypeError, ValidationError, ValueError):
        return False


def screen_valid(data: dict[str, Any]) -> bool:
    try:
        markup = InlineKeyboardMarkup.model_validate(data.get("screen_markup"))
        return bool(markup.inline_keyboard)
    except ValidationError:
        return False


def rotated_markup(current: str, data: dict[str, Any], flow: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup.model_validate(data["screen_markup"])
    group = current.split(":")[0]
    generation_names = FORM_ACTIONS.get(group, set())
    for row in markup.inline_keyboard:
        for control in row:
            if not control.callback_data:
                continue
            try:
                action = Action.unpack(control.callback_data)
            except (ValueError, TypeError):
                continue
            if group == "ConfirmationForm" and action.name in generation_names:
                action.value = str(flow)
            elif group == "PenaltyForm" and action.name in {"penalty_type", "penalty_kinds"}:
                action.value = str(flow)
            elif action.name in generation_names:
                action.version = flow
            control.callback_data = action.pack()
    return markup


async def interrupt(bot: Bot, actor: User, state: FSMContext, destination: Action) -> None:
    data = await state.get_data()
    pending = data.get("nav_pending")
    original = (
        pending["data"]
        if isinstance(pending, dict) and isinstance(pending.get("data"), dict)
        else {key: value for key, value in data.items() if key != "nav_pending"}
    )
    nonce = fresh_nonce(pending.get("nonce") if isinstance(pending, dict) else None)
    await state.update_data(
        nav_pending={"nonce": nonce, "destination": destination.model_dump(), "data": original}
    )
    labels = {
        "new": "новой задаче",
        "list": "задачам",
        "ratings": "входящим",
        "templates": "шаблонам",
        "results": "результатам",
    }
    await render(
        bot,
        actor.telegram_id,
        state,
        f"Перейти к {labels.get(destination.name, 'выбранному экрану')}?\nНезавершённая форма будет удалена.",
        keyboard(
            [button("Уйти и удалить", "nav_leave", version=nonce)],
            [button("Остаться", "nav_stay", version=nonce)],
        ),
    )


async def destination_screen(
    action: Action, bot: Bot, actor: User, state: FSMContext, ui: UIService
) -> None:
    """Open the same screen/form as its inline entry without fabricating a callback."""
    from app.bot.handlers.common import form
    from app.bot.handlers.penalty_prompts import target_prompt as penalty_target_prompt
    from app.bot.handlers.settings_prompts import setting_prompt, type_prompt
    from app.bot.handlers.task_prompts import target_prompt
    from app.bot.handlers.templates import template_details, template_list

    await reset(state, keep_navigation=False)
    if action.name == "menu":
        # A fresh menu message stays at the bottom as updates accumulate above it.
        await state.update_data(message_id=None)
    if action.name == "new":
        peer = next(u for u in await ui.tasks.user_repo.participants() if u.id != actor.id)
        await form(state, target="SELF", peer_name=peer.display_name)
        await target_prompt(bot, actor, state)
        return
    if action.name == "penalty":
        await form(state)
        await penalty_target_prompt(bot, actor, state, ui.penalties)
        return
    if action.name == "type_new":
        await form(state)
        await type_prompt(bot, actor, state, "name")
        return
    if action.name == "user_setting":
        if action.value not in {"timezone", "weekly_target"}:
            raise DomainError("Недопустимая настройка.")
        await form(state, field=action.value)
        await setting_prompt(bot, actor, state, action.value)
        return
    if action.name == "templates":
        await template_list(bot, actor, state, ui, action.entity)
        return
    if action.name == "tpl_view":
        await template_details(bot, actor, state, ui, action.entity)
        return
    if action.name == "list":
        await state.update_data(filter=action.value or "mine_all", page=action.entity)
        screen = await ui.task_list(actor, action.value or "mine_all", action.entity)
    elif action.name == "ratings":
        screen = await ui.rating(actor, action.entity)
    elif action.name == "results":
        try:
            offset = int(action.value or "0")
        except ValueError:
            raise DomainError("Недопустимая неделя.") from None
        await state.update_data(results_offset=offset)
        screen = await ui.results_screen(offset)
    elif action.name == "task":
        screen = await ui.task_details(actor, action.entity)
    elif action.name == "history":
        screen = await ui.history(action.entity)
    elif action.name == "settings":
        screen = await ui.settings_screen(actor)
    elif action.name == "types":
        await state.update_data(types_page=action.entity)
        screen = await ui.penalty_types(action.entity, True)
    elif action.name == "type_view":
        screen = await ui.type_details(action.entity)
    elif action.name == "penalty_list":
        await state.update_data(penalty_page=action.entity)
        screen = await ui.penalty_list(actor, action.entity)
    else:
        screen = await ui.menu(actor)
    await render(bot, actor.telegram_id, state, *screen)


async def handle_navigation(event: Message | CallbackQuery, data: dict[str, Any]) -> bool:
    bot, state, actor, ui = data["bot"], data["state"], data["actor"], data["ui"]
    current = await state.get_state()
    saved = await state.get_data()
    pending = saved.get("nav_pending")
    command = await message_destination(event, bot) if isinstance(event, Message) else None
    if command == "start":
        await reset(state, keep_navigation=False)
        await bot.send_message(
            actor.telegram_id, "Открываю TaskFlow.", reply_markup=ReplyKeyboardRemove()
        )
        await destination_screen(DESTINATIONS["menu"], bot, actor, state, ui)
        if isinstance(event, Message):
            await discard_input(event)
        return True
    if command in {"menu", "cancel"}:
        await destination_screen(DESTINATIONS[command], bot, actor, state, ui)
        if isinstance(event, Message):
            await discard_input(event)
        return True
    if "nav_pending" in saved and (not pending_valid(current, pending) or not screen_valid(saved)):
        await reset(state, keep_navigation=False)
        await render(bot, actor.telegram_id, state, *await ui.menu(actor))
        if isinstance(event, CallbackQuery):
            await answer_callback(event, "Меню восстановлено.")
        else:
            await discard_input(event)
        return True
    if not draft_valid(current, saved):
        return False  # Existing corruption recovery owns this case.
    if command:
        if current:
            await interrupt(bot, actor, state, DESTINATIONS[command])
        else:
            await destination_screen(DESTINATIONS[command], bot, actor, state, ui)
        if isinstance(event, Message):
            await discard_input(event)
        return True
    if isinstance(event, CallbackQuery):
        try:
            action = Action.unpack(event.data or "")
        except (ValueError, TypeError):
            action = None
        if pending:
            visible = {
                b.get("callback_data")
                for row in saved["screen_markup"]["inline_keyboard"]
                for b in row
            }
            if (
                action
                and action.name in {"nav_leave", "nav_stay"}
                and action.version == pending["nonce"]
                and event.data in visible
                and getattr(event.message, "message_id", None) == saved.get("message_id")
            ):
                await answer_callback(event)
                if action.name == "nav_leave":
                    await destination_screen(
                        Action.model_validate(pending["destination"]), bot, actor, state, ui
                    )
                else:
                    original = pending["data"]
                    flow = fresh_nonce(original.get("flow"))
                    markup = rotated_markup(current, original, flow)
                    # Keep the live message cache so render actually replaces the prompt.
                    original = {
                        **original,
                        "flow": flow,
                        "message_id": saved.get("message_id"),
                        "rendered_text": saved.get("rendered_text"),
                    }
                    await state.set_data(original)
                    await render(bot, actor.telegram_id, state, original["screen_text"], markup)
                return True
            if action and action.name == "menu":
                await answer_callback(event)
                await destination_screen(DESTINATIONS["menu"], bot, actor, state, ui)
                return True
            await answer_callback(
                event, "Сначала выбери «Остаться» или «Уйти и удалить».", show_alert=True
            )
            return True
        if (
            current
            and action
            and action.name in (NAVIGATION | NEW_FORMS)
            and action.name != "menu"
            and getattr(event.message, "message_id", None) != saved.get("message_id")
        ):
            await answer_callback(event)
            await interrupt(bot, actor, state, action)
            return True
    if pending and isinstance(event, Message):
        await discard_input(event)
        return True
    return False
