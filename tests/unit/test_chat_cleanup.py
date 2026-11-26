from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageReplyMarkup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.bot.callbacks.actions import Action
from app.bot.storage import cleanup_legacy_keyboards
from tests.fakes import TelegramSession


def legacy_markup():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Old app", web_app=WebAppInfo(url="https://example.com/miniapp")
                ),
                InlineKeyboardButton(text="Old link", url="https://example.com/miniapp?task=1"),
                InlineKeyboardButton(text="Menu", callback_data=Action(name="menu").pack()),
            ]
        ]
    )


@pytest.mark.parametrize("rejected", [False, True])
async def test_known_keyboard_cleanup_is_scoped_and_retries_failed_edits(settings, rejected):
    api = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=api)
    original = api.make_request

    async def request(bot, method, timeout=None):  # noqa: ASYNC109
        if rejected and isinstance(method, EditMessageReplyMarkup) and method.chat_id == 1001:
            raise TelegramBadRequest(method, "message can't be edited")
        return await original(bot, method, timeout)

    api.make_request = request
    raw = legacy_markup().model_dump(mode="json")
    rows = {
        f"{bot.id}:{chat}:{chat}:default": SimpleNamespace(
            data={
                "message_id": chat + 10,
                "screen_markup": raw,
                "nav_pending": {"data": {"screen_markup": raw}},
            }
        )
        for chat in (1001, 1002, 9999)
    }
    session = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(side_effect=lambda model, key: rows.get(key)),
        flush=AsyncMock(),
    )
    await cleanup_legacy_keyboards(session, bot, settings)
    assert "pg_advisory_xact_lock(78421310)" in str(session.execute.call_args.args[0])
    assert {call.args[1] for call in session.get.call_args_list} == {
        f"{bot.id}:1001:1001:default",
        f"{bot.id}:1002:1002:default",
    }
    calls = [call for call in api.calls if isinstance(call, EditMessageReplyMarkup)]
    assert {call.chat_id for call in calls} == ({1002} if rejected else {1001, 1002})
    for call in calls:
        assert len(call.reply_markup.inline_keyboard[0]) == 1
        assert call.reply_markup.inline_keyboard[0][0].callback_data == Action(name="menu").pack()
    for chat in (1001, 1002):
        data = rows[f"{bot.id}:{chat}:{chat}:default"].data
        assert data["screen_markup"] == data["nav_pending"]["data"]["screen_markup"]
        assert len(data["screen_markup"]["inline_keyboard"][0]) == (
            3 if rejected and chat == 1001 else 1
        )
    assert rows[f"{bot.id}:9999:9999:default"].data["screen_markup"] == raw
    await bot.session.close()


@pytest.mark.parametrize("message_id,raw", [(None, {}), (True, {}), (-1, {}), (12, {"bad": True})])
async def test_cleanup_ignores_invalid_saved_ids_and_markup(settings, message_id, raw):
    api = TelegramSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=api)
    session = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(
            return_value=SimpleNamespace(
                data={
                    "message_id": message_id,
                    "screen_markup": raw,
                }
            )
        ),
        flush=AsyncMock(),
    )
    await cleanup_legacy_keyboards(session, bot, settings)
    assert not api.calls
    await bot.session.close()
