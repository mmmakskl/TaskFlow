import logging
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InputRichMessage, Message

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


def rich_content(text: str) -> InputRichMessage:
    """Existing screens use escaped HTML with newlines; preserve those line breaks."""
    return InputRichMessage(html="<p>" + text.replace("\n", "<br>") + "</p>")


def rich_unsupported(exc: TelegramBadRequest | TelegramNotFound) -> bool:
    """Only retry after an explicit rejection, never after an ambiguous transport failure."""
    message = str(exc).lower()
    if isinstance(exc, TelegramNotFound) and exc.message.strip().lower() == "not found":
        return True
    return any(
        fragment in message
        for fragment in (
            "method not found",
            "method is not available",
            "rich messages are not supported",
            "rich message is not supported",
            "rich_message is not supported",
        )
    )


async def render(
    bot: Bot, chat_id: int, state: FSMContext, text: str, markup: InlineKeyboardMarkup
) -> None:
    data = await state.get_data()
    message_id = data.get("message_id")
    use_rich = not data.get("rich_unsupported", False)
    if message_id:
        try:
            if data.get("rendered_text") == text:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=int(message_id), reply_markup=markup
                )
            else:
                try:
                    if use_rich:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=int(message_id),
                            rich_message=rich_content(text),
                            reply_markup=markup,
                        )
                    else:
                        await bot.edit_message_text(
                            text=text,
                            chat_id=chat_id,
                            message_id=int(message_id),
                            reply_markup=markup,
                            parse_mode="HTML",
                        )
                except (TelegramBadRequest, TelegramNotFound) as exc:
                    if not use_rich or not rich_unsupported(exc):
                        raise
                    await state.update_data(rich_unsupported=True)
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
    if use_rich:
        try:
            message = await bot.send_rich_message(chat_id, rich_content(text), reply_markup=markup)
        except (TelegramBadRequest, TelegramNotFound) as exc:
            if not rich_unsupported(exc):
                raise
            await state.update_data(rich_unsupported=True)
            message = await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    else:
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
