from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import Chat, InlineKeyboardMarkup, Message


class TelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.next_id = 100
        self.expired_ack = False

    async def make_request(
        self, bot: Any, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery) and self.expired_ack:
            raise TelegramBadRequest(
                method=method,
                message="query is too old and response timeout expired or query ID is invalid",
            )
        if isinstance(method, (SendMessage, EditMessageText)):
            self.next_id += 1
            return Message(
                message_id=getattr(method, "message_id", None) or self.next_id,
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
                reply_markup=(
                    method.reply_markup
                    if isinstance(method.reply_markup, InlineKeyboardMarkup)
                    else None
                ),
            )
        return True

    async def close(self) -> None:
        pass

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield b""
