from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app.services.rules import DomainError


def safe(value: Any) -> str:
    return escape(str(value))


def progress(score: int, target: int) -> str:
    percent = max(0, min(100, int(score * 100 / target)))
    filled = percent // 10
    return f"{'█' * filled}{'░' * (10 - filled)} {percent}%"


async def render(
    bot: Bot, chat_id: int, state: FSMContext, text: str, markup: InlineKeyboardMarkup
) -> None:
    data = await state.get_data()
    message_id = data.get("message_id")
    if message_id:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=int(message_id),
                reply_markup=markup,
                parse_mode="HTML",
            )
            return
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "message is not modified" in error:
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
    await state.update_data(message_id=message.message_id)


async def reset(state: FSMContext, **data: Any) -> None:
    previous = await state.get_data()
    await state.set_state(None)
    await state.set_data({"message_id": previous.get("message_id"), **data})


async def require_current(
    state: FSMContext, expected: str | None, flow: int | None = None
) -> dict[str, Any]:
    if await state.get_state() != expected:
        raise DomainError("Эта кнопка устарела. Открой меню и попробуй снова.")
    data = await state.get_data()
    if flow is not None and data.get("flow") != flow:
        raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
    return data
