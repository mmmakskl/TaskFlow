from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Dialog

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
