"""Startup cleanup waits for old workers and preserves independently saved draft controls."""

import asyncio

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import text

from app.bot.callbacks.actions import Action
from app.bot.storage import cleanup_legacy_keyboards
from app.models.entities import Dialog
from tests.fakes import TelegramSession

pytestmark = pytest.mark.integration


def markup(name):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="App", web_app=WebAppInfo(url="https://example.com/miniapp")
                ),
                InlineKeyboardButton(text=name, callback_data=Action(name=name).pack()),
            ]
        ]
    ).model_dump(mode="json")


async def test_cleanup_serializes_with_old_worker_and_keeps_pending_draft(factory, settings):
    api = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=api)
    key = f"{bot.id}:1001:1001:default"
    async with factory.begin() as session:
        session.add(
            Dialog(
                key=key,
                state="TaskForm:title",
                data={
                    "message_id": 10,
                    "screen_markup": markup("menu"),
                    "title": "Before",
                },
            )
        )

    async def cleanup():
        async with factory.begin() as session:
            await cleanup_legacy_keyboards(session, bot, settings)

    async with factory.begin() as worker:
        await worker.execute(text("SELECT pg_advisory_xact_lock(78421310)"))
        row = await worker.get(Dialog, key)
        task = asyncio.create_task(cleanup())
        try:
            # An older replica owns the inbox lock; cleanup must wait before reading its FSM.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
            assert not api.calls
            row.data = {
                "message_id": 20,
                "screen_markup": markup("nav_stay"),
                "title": "Newer draft",
                "nav_pending": {
                    "data": {
                        "screen_markup": markup("task_back"),
                        "title": "Original draft",
                    }
                },
            }
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    await asyncio.wait_for(task, timeout=5)
    calls = [call for call in api.calls if isinstance(call, EditMessageReplyMarkup)]
    assert len(calls) == 1 and calls[0].message_id == 20
    assert calls[0].reply_markup.inline_keyboard[0][0].text == "nav_stay"
    async with factory() as session:
        row = await session.get(Dialog, key)
        assert row.state == "TaskForm:title" and row.data["title"] == "Newer draft"
        pending = row.data["nav_pending"]["data"]
        assert pending["title"] == "Original draft"
        assert [b["text"] for b in pending["screen_markup"]["inline_keyboard"][0]] == ["task_back"]
    await bot.session.close()
