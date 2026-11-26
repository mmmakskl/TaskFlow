import logging
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from app.services.rules import DomainError


def safe(value: Any) -> str:
    return escape(str(value))


def short(value: str, maximum: int) -> str:
    """Keep button labels on one line; full text remains in details."""
    text = " ".join(value.split())
    return text if len(text) <= maximum else text[: maximum - 1].rstrip() + "…"


def progress(score: int, target: int) -> str:
    percent = max(0, int(score * 100 / target)) if target > 0 else 0
    filled = min(100, percent) // 10
    return f"{'█' * filled}{'░' * (10 - filled)} {percent}%"


def chat_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Never replay legacy app controls from durable FSM screens or error prompts."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            controls
            for row in markup.inline_keyboard
            if (
                controls := [
                    control
                    for control in row
                    if control.web_app is None and not (control.url and "/miniapp" in control.url)
                ]
            )
        ]
    )


async def render(
    bot: Bot, chat_id: int, state: FSMContext, text: str, markup: InlineKeyboardMarkup
) -> None:
    markup = chat_markup(markup)
    data = await state.get_data()
    message_id = data.get("message_id")
    if message_id:
        try:
            if data.get("rendered_text") == text:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=int(message_id), reply_markup=markup
                )
            else:
                await bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=int(message_id),
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            await state.update_data(
                screen_text=text, rendered_text=text, screen_markup=markup.model_dump(mode="json")
            )
            return
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "message is not modified" in error:
                await state.update_data(
                    screen_text=text,
                    rendered_text=text,
                    screen_markup=markup.model_dump(mode="json"),
                )
                return
            if not any(
                fragment in error
                for fragment in (
                    "message to edit not found",
                    "message can't be edited",
                    "message_id_invalid",
                )
            ):
                raise
    message = await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    await state.update_data(
        message_id=message.message_id,
        screen_text=text,
        rendered_text=text,
        screen_markup=markup.model_dump(mode="json"),
    )


NAVIGATION_KEYS = (
    "filter",
    "page",
    "types_page",
    "penalty_page",
    "results_offset",
    "templates_page",
)
SCREEN_KEYS = ("message_id", "screen_text", "screen_markup", "rendered_text", "rich_unsupported")


async def reset(state: FSMContext, keep_navigation: bool = True, **data: Any) -> None:
    previous = await state.get_data()
    keys = SCREEN_KEYS + ("keyboard_ready",) + (NAVIGATION_KEYS if keep_navigation else ())
    await state.set_state(None)
    await state.set_data({**{key: previous[key] for key in keys if key in previous}, **data})


async def require_current(
    state: FSMContext, expected: str | None, flow: int | None = None
) -> dict[str, Any]:
    if await state.get_state() != expected:
        raise DomainError("Эта кнопка устарела. Открой меню и попробуй снова.")
    data = await state.get_data()
    if flow is not None and data.get("flow") != flow:
        raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
    return data


async def validation_error(bot: Bot, chat_id: int, state: FSMContext, error: str) -> None:
    from app.bot.keyboards.common import cancel

    data = await state.get_data()
    base = data.get("screen_text", "")
    markup = (
        InlineKeyboardMarkup.model_validate(data["screen_markup"])
        if data.get("screen_markup")
        else cancel()
    )
    text = f"⚠️ {safe(error)}" + (f"\n\n{base}" if base else "")
    await render(bot, chat_id, state, text, markup)
    # Keep the original prompt, so repeated errors never accumulate in the message.
    await state.update_data(screen_text=base)


async def discard_input(message: Message) -> None:
    """Chat cleanup is optional: a network failure must never undo a saved action."""
    try:
        await message.delete()
    except (TelegramAPIError, TelegramNetworkError):
        logging.getLogger(__name__).debug("input_cleanup_failed")
