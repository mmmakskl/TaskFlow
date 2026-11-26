from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SetChatMenuButton, SetMyCommands
from aiogram.types import Chat, Message

from app.bot.callbacks.actions import Action
from app.bot.handlers.common import confirm_action, confirmed_action
from app.bot.keyboards.common import button, keyboard
from app.bot.navigation import (
    COMMANDS,
    QUICK_KEYS,
    message_destination,
    pending_valid,
    register_commands,
    rotated_markup,
)
from app.bot.states.forms import ConfirmationForm
from tests.fakes import TelegramSession


@pytest.fixture
async def state():
    storage = MemoryStorage()
    yield FSMContext(storage, StorageKey(bot_id=1, chat_id=1001, user_id=1001))
    await storage.close()


@pytest.mark.parametrize("text", [*QUICK_KEYS, *(f"/{name}" for name in COMMANDS)])
async def test_all_reserved_inputs(text):
    bot = Bot("123456789:TEST_ONLY_TOKEN")
    message = Message(message_id=1, date=0, chat=Chat(id=1001, type="private"), text=text)
    assert await message_destination(message, bot) == QUICK_KEYS.get(text, text.removeprefix("/"))


@pytest.mark.parametrize(
    "group,names",
    [
        ("TaskForm:draft", ["draft_field", "draft_target", "task_save", "task_back"]),
        ("SettingsForm:target", ["setting_value", "settings_back", "type_clear"]),
        ("TemplateForm:field", ["tpl_back", "tpl_none"]),
        ("PenaltyForm:confirm", ["penalty_commit", "penalty_back", "penalty_value"]),
    ],
)
def test_restoration_rotates_generation_controls_only(group, names):
    markup = keyboard(
        *[[button(name, name, version=9)] for name in names],
        [button("Back to entity", "edit", 1, 9)],
    )
    data = {"flow": 9, "screen_markup": markup.model_dump(mode="json")}
    restored = rotated_markup(group, data, 10)
    actions = [Action.unpack(row[0].callback_data) for row in restored.inline_keyboard]
    assert all(a.version == 10 for a in actions[:-1])
    assert actions[-1].version == 9
    assert data["screen_markup"] == markup.model_dump(mode="json")


def test_penalty_entity_version_coinciding_with_generation_stays_real():
    markup = keyboard(
        [button("Kind", "penalty_type", 3, 9, "9")], [button("Page", "penalty_kinds", 1, value="9")]
    )
    restored = rotated_markup("PenaltyForm:kind", {"screen_markup": markup.model_dump()}, 10)
    kind = Action.unpack(restored.inline_keyboard[0][0].callback_data)
    page = Action.unpack(restored.inline_keyboard[1][0].callback_data)
    assert (kind.version, kind.value, kind.entity) == (9, "10", 3)
    assert (page.version, page.value, page.entity) == (0, "10", 1)


@pytest.mark.parametrize(
    "name",
    [
        "status_confirm",
        "rerate_confirm",
        "reject_confirm",
        "tpl_disable_confirm",
        "type_toggle_confirm",
        "penalty_delete_confirm",
    ],
)
async def test_confirmation_restores_nonce_without_corrupting_entity_version(state, name):
    action = await confirm_action(state, name, 1, 9, points=500)
    await state.update_data(flow=9)
    action.value = "9"
    markup = keyboard([("Confirm", action.pack())])
    original = {**await state.get_data(), "screen_markup": markup.model_dump()}
    restored = rotated_markup(ConfirmationForm.action.state, original, 10)
    fresh = Action.unpack(restored.inline_keyboard[0][0].callback_data)
    await state.update_data(flow=10)
    assert fresh.version == 9 and fresh.value == "10"
    assert (await confirmed_action(state, fresh))["version"] == 9
    from app.services.rules import DomainError

    with pytest.raises(DomainError):
        await confirmed_action(state, action)


@pytest.mark.parametrize(
    "pending", [None, {}, {"nonce": 1, "data": []}, {"nonce": "1", "destination": {}, "data": {}}]
)
def test_corrupt_pending_rejected(pending):
    assert not pending_valid("TaskForm:title", pending)


async def test_scoped_registration_continues_after_failure():
    api = TelegramSession()
    bot = Bot("123456789:TEST_ONLY_TOKEN", session=api)
    original = api.make_request

    async def request(bot, method, timeout=None):  # noqa: ASYNC109 - transport signature
        if isinstance(method, SetMyCommands) and getattr(method.scope, "chat_id", None) == 1001:
            raise TelegramNetworkError(method=method, message="offline")
        return await original(bot, method, timeout)

    api.make_request = request
    await register_commands(bot, [1001, 1002])
    registrations = [call for call in api.calls if isinstance(call, SetMyCommands)]
    assert {getattr(call.scope, "chat_id", None) for call in registrations} == {None, 1002}
    assert all([c.command for c in call.commands] == list(COMMANDS) for call in registrations)
    menus = [call for call in api.calls if isinstance(call, SetChatMenuButton)]
    assert {call.chat_id for call in menus} == {None, 1001, 1002}
    assert all(call.menu_button.type == "commands" for call in menus)
    await bot.session.close()


def test_fresh_nonce_never_reuses_previous_even_on_random_collision(monkeypatch):
    from app.bot import navigation

    monkeypatch.setattr(navigation, "randbelow", lambda _: 8)
    assert navigation.fresh_nonce(9) == 10
    assert navigation.fresh_nonce(8) == 9


@pytest.mark.parametrize("mention,expected", [("TaskFlowBot", "new"), ("other_bot", None)])
async def test_bot_command_mentions_resolve_only_matching_bot(monkeypatch, mention, expected):
    from aiogram.types import User

    bot = Bot("123456789:TEST_ONLY_TOKEN")
    monkeypatch.setattr(
        bot,
        "me",
        AsyncMock(
            return_value=User(
                id=123456789, is_bot=True, first_name="TaskFlow", username="TaskFlowBot"
            )
        ),
    )
    message = Message(
        message_id=1, date=0, chat=Chat(id=1001, type="private"), text=f"/new@{mention}"
    )
    assert await message_destination(message, bot) == expected
