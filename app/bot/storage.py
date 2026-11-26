import logging
from collections.abc import Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Dialog

if TYPE_CHECKING:
    from aiogram import Bot

    from app.config.settings import Settings

current_session: ContextVar[AsyncSession] = ContextVar("current_session")


class PostgresStorage(BaseStorage):
    """FSM participates in the inbox transaction, surviving restart and deploy."""

    async def dialog(self, key: StorageKey) -> Dialog:
        session = current_session.get()
        name = f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"
        await session.execute(insert(Dialog).values(key=name, data={}).on_conflict_do_nothing())
        dialog = await session.get(Dialog, name)
        assert dialog is not None
        return dialog

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        dialog = await self.dialog(key)
        dialog.state = state.state if isinstance(state, State) else state
        await current_session.get().flush()

    async def find_dialog(self, key: StorageKey) -> Dialog | None:
        # FSM loads before the access middleware. Reads must not allocate rows
        # for unknown senders or group chats; only authorized handlers write.
        name = f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"
        return await current_session.get().get(Dialog, name)

    async def get_state(self, key: StorageKey) -> str | None:
        dialog = await self.find_dialog(key)
        return dialog.state if dialog else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        (await self.dialog(key)).data = dict(data)
        await current_session.get().flush()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        dialog = await self.find_dialog(key)
        return dict(dialog.data) if dialog else {}

    async def close(self) -> None:
        pass


async def cleanup_legacy_keyboards(session: AsyncSession, bot: "Bot", settings: "Settings") -> None:
    """Edit only persisted private-chat screen IDs; Telegram history is never scanned."""
    from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
    from aiogram.types import InlineKeyboardMarkup

    from app.bot.render import chat_markup

    # Match inbox processing across replicas before reading any FSM snapshot.
    # A row lock alone cannot stop an old worker overwriting data it already read.
    await session.execute(text("SELECT pg_advisory_xact_lock(78421310)"))
    for chat_id in settings.authorized_ids:
        key = f"{bot.id}:{chat_id}:{chat_id}:default"
        dialog = await session.get(Dialog, key)
        if dialog is None or not isinstance(dialog.data, dict):
            continue
        data = dict(dialog.data)
        message_id = data.get("message_id")
        raw = data.get("screen_markup")
        if type(message_id) is not int or message_id <= 0 or not isinstance(raw, dict):
            continue
        try:
            original = InlineKeyboardMarkup.model_validate(raw)
        except ValueError:
            continue
        markup = chat_markup(original)
        if markup == original:
            continue
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
        except (TelegramAPIError, TelegramNetworkError):
            # Keep the known ID for a later startup retry; render also sanitizes cached markup.
            logging.getLogger(__name__).warning("legacy_keyboard_cleanup_failed")
            continue
        data["screen_markup"] = markup.model_dump(mode="json")
        # A pending navigation snapshot may contain the same legacy screen.
        pending = data.get("nav_pending")
        if isinstance(pending, dict) and isinstance(pending.get("data"), dict):
            original_data = dict(pending["data"])
            try:
                pending_markup = InlineKeyboardMarkup.model_validate(
                    original_data.get("screen_markup")
                )
            except ValueError:
                pass  # Existing corruption recovery owns malformed snapshots.
            else:
                original_data["screen_markup"] = chat_markup(pending_markup).model_dump(mode="json")
                data["nav_pending"] = {**pending, "data": original_data}
        dialog.data = data
    await session.flush()
