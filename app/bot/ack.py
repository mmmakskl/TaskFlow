from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


async def answer_callback(
    query: CallbackQuery, text: str | None = None, show_alert: bool = False
) -> None:
    """An expired Telegram spinner acknowledgment must not roll back business writes."""
    try:
        await query.answer(text, show_alert=show_alert)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "query is too old" not in message and "query id is invalid" not in message:
            raise
