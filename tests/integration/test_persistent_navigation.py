"""Permanent navigation through real handlers, services and durable PostgreSQL FSM."""

from copy import deepcopy

import pytest
from aiogram.methods import SendMessage, SetMyCommands
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.bot.callbacks.actions import Action
from app.bot.dispatcher import create_dispatcher
from app.models import Penalty, ScoreEvent, Task, TaskTemplate
from app.models.entities import Dialog
from app.web.application import create_app
from tests.integration.test_form_navigation import data_for
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


async def dialog(driver, user=1001):
    async with driver.factory() as session:
        row = await session.scalar(select(Dialog).where(Dialog.key.like(f"%:{user}:{user}:%")))
        return row.state, dict(row.data)


async def enter(driver, kind):
    await driver.text(1001, "/menu")
    if kind.startswith("task"):
        await driver.text(1001, "/new")
        if kind != "task-choice":
            await driver.press(1001, "Себе")
        if kind not in {"task-choice", "task-title"}:
            await driver.text(1001, "Untouched draft")
        if kind == "task-comment":
            await driver.press(1001, "Комментарий")
            await driver.text(1001, "Draft comment")
            await driver.press(1001, "Комментарий")
            await driver.press(1001, "Добавить комментарий")
        if kind == "task-error":
            await driver.press(1001, "Баллы")
            await driver.text(1001, "bad")
            assert "⚠️" in driver.screen(1001).text
    elif kind == "template":
        await driver.text(1001, "/templates")
        await driver.press(1001, "Новый шаблон")
    elif kind == "settings":
        await driver.press(1001, "Настройки")
        await driver.press(1001, "Недельная цель")
    elif kind.startswith("penalty"):
        await driver.press(1001, "Штраф")
        await driver.press(1001, "Макс")
        if kind != "penalty-kind":
            await driver.press(1001, "Instagram")
        if kind == "penalty-confirm":
            await driver.text(1001, "12")
            await driver.press(1001, "Пропустить")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("🏠 Меню", "НЕДЕЛЯ"),
        ("➕ Задача", "Для кого"),
        ("📋 Задачи", "Мои • 0"),
        ("📥 Входящие", "входящих"),
        ("⚡ Шаблоны", "Шаблоны"),
        ("🏆 Результаты", "Разница"),
        ("/menu", "НЕДЕЛЯ"),
        ("/new", "Для кого"),
        ("/tasks", "Мои • 0"),
        ("/inbox", "входящих"),
        ("/templates", "Шаблоны"),
        ("/results", "Разница"),
    ],
)
async def test_every_quick_action_and_command_destination(factory, settings, text, expected):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.text(1001, text)
    assert expected.lower() in driver.screen(1001).text.lower()
    carriers = [
        c
        for c in driver.api.calls
        if isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardMarkup)
    ]
    assert not carriers
    state, data = await dialog(driver)
    assert isinstance(data["message_id"], int)
    assert "nav_pending" not in data
    assert bool(state) == (text in {"/new", "➕ Задача"})
    await driver.bot.session.close()


@pytest.mark.parametrize(
    "kind",
    [
        "task-choice",
        "task-title",
        "task-draft",
        "task-comment",
        "task-error",
        "template",
        "settings",
        "penalty-kind",
        "penalty-quantity",
        "penalty-confirm",
    ],
)
async def test_any_form_stay_preserves_data_refreshes_controls_and_survives_restart(
    factory, settings, kind
):
    driver = BotDriver(factory, settings)
    await enter(driver, kind)
    current, original = await dialog(driver)
    old_controls = [
        b.callback_data
        for row in driver.screen(1001).reply_markup.inline_keyboard
        for b in row
        if b.callback_data and Action.unpack(b.callback_data).version
    ]
    await driver.text(1001, "📋 Задачи")
    pending_state, pending = await dialog(driver)
    assert pending_state == current
    assert pending["nav_pending"]["data"] == original
    old_leave = data_for(driver, 1001, "Уйти и удалить")
    old_stay = data_for(driver, 1001, "Остаться")
    await driver.text(1001, "/results")
    assert (await dialog(driver))[1]["nav_pending"]["data"] == original
    await driver.press(1001, data=old_leave)
    await driver.press(1001, data=old_stay)
    assert "Незавершённая форма" in driver.screen(1001).text
    # A new dispatcher reads the same serialized form and prompt from PostgreSQL.
    driver.dp = create_dispatcher(settings)
    fresh_stay = data_for(driver, 1001, "Остаться")
    await driver.press(1001, "Остаться")
    restored_state, restored = await dialog(driver)
    assert restored_state == current
    assert restored["flow"] != original["flow"]
    assert restored["screen_text"] == original["screen_text"]
    assert "⚠️" not in driver.screen(1001).text or "⚠️" in original["screen_text"]
    for key, value in original.items():
        if key not in {"flow", "screen_markup", "rendered_text"}:
            assert restored[key] == value, key
    await driver.press(1001, data=fresh_stay)
    assert (await dialog(driver))[0] == current
    for old in old_controls:
        await driver.press(1001, data=old)
        assert (await dialog(driver))[0] == current
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(select(func.count(Penalty.id))) == 0
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
        assert await session.scalar(select(func.count(TaskTemplate.id))) == 2
    # Fresh controls genuinely work; a real mutation at the restored confirmation is possible.
    if kind == "penalty-confirm":
        await driver.press(1001, "Назначить")
        async with factory() as session:
            assert await session.scalar(select(func.count(Penalty.id))) == 1
    elif kind == "penalty-kind":
        await driver.press(1001, "Instagram")
        assert "quantity" in (await dialog(driver))[0]
    elif kind == "settings":
        await driver.press(1001, "15 000")
        assert (await dialog(driver))[0] is None
    elif kind == "task-draft":
        await driver.press(1001, "✅ Создать")
        async with factory() as session:
            assert await session.scalar(select(func.count(Task.id))) == 1
    elif kind == "template":
        await driver.text(1001, "Template restored")
        assert (await dialog(driver))[1]["title"] == "Template restored"
    await driver.bot.session.close()


@pytest.mark.parametrize(
    "text",
    [
        "➕ Задача",
        "📋 Задачи",
        "📥 Входящие",
        "⚡ Шаблоны",
        "🏆 Результаты",
        "/new",
        "/tasks",
        "/inbox",
        "/templates",
        "/results",
    ],
)
async def test_reserved_text_never_enters_title_or_choice_and_leave_invalidates_old_buttons(
    factory, settings, text
):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-choice" if text.startswith("/") else "task-title")
    _, before = await dialog(driver)
    old = driver.screen(1001).reply_markup.inline_keyboard[0][0].callback_data
    await driver.text(1001, text)
    _, pending = await dialog(driver)
    assert pending["nav_pending"]["data"] == before
    await driver.press(1001, data=old)
    assert "Незавершённая форма" in driver.screen(1001).text
    leave = await driver.press(1001, "Уйти и удалить")
    state, after = await dialog(driver)
    assert "nav_pending" not in after and after.get("title") is None
    await driver.press(1001, data=leave)
    await driver.press(1001, data=old)
    assert (await dialog(driver))[0] == state
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
    await driver.bot.session.close()


@pytest.mark.parametrize("exit_text", ["🏠 Меню", "/menu", "/cancel", "/start"])
async def test_direct_exit_discards_even_corrupt_draft(factory, settings, exit_text):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    async with factory.begin() as session:
        row = await session.scalar(select(Dialog))
        row.data = {**row.data, "flow": "broken", "nav_pending": []}
    await driver.text(1001, exit_text)
    assert ("WEBHOOK_BASE_URL" if exit_text == "/start" else "НЕДЕЛЯ") in driver.screen(1001).text
    state, data = await dialog(driver)
    assert state is None and "title" not in data and "nav_pending" not in data
    await driver.bot.session.close()


async def test_confirmation_stay_keeps_entity_version_and_new_nonce_mutates_once(factory, settings):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    await driver.press(1001, "✅ Создать")
    await driver.text(1002, "/menu")
    await driver.text(1002, "/inbox")
    await driver.press(1002, "Отклонить")
    current, original = await dialog(driver, 1002)
    stale = data_for(driver, 1002, "✅ Отклонить")
    await driver.text(1002, "/templates")
    await driver.press(1002, "Остаться")
    assert (await dialog(driver, 1002))[1]["confirmation"] == original["confirmation"]
    fresh = data_for(driver, 1002, "✅ Отклонить")
    assert Action.unpack(fresh).version == Action.unpack(stale).version
    assert Action.unpack(fresh).value != Action.unpack(stale).value
    await driver.press(1002, data=stale)
    async with factory() as session:
        assert (await session.scalar(select(Task))).status == "BACKLOG"
    await driver.press(1002, data=fresh)
    await driver.press(1002, data=fresh)
    async with factory() as session:
        assert (await session.scalar(select(Task))).status == "CANCELLED"
    assert current == "ConfirmationForm:action"
    await driver.bot.session.close()


@pytest.mark.parametrize(
    "action",
    [
        Action(name="list", value="shared_all"),
        Action(name="ratings"),
        Action(name="templates"),
        Action(name="history"),
        Action(name="results"),
        Action(name="settings"),
        Action(name="types"),
        Action(name="penalty_list"),
        Action(name="penalty"),
        Action(name="type_new"),
        Action(name="user_setting", value="weekly_target"),
    ],
)
async def test_notification_navigation_during_draft_retains_current_screen_until_choice(
    factory, settings, action
):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    _, original = await dialog(driver)
    await driver.put(
        {
            "callback_query": {
                "id": "notification",
                "from": {"id": 1001, "is_bot": False, "first_name": "Max"},
                "chat_instance": "test",
                "data": action.pack(),
                "message": {
                    "message_id": 99999,
                    "date": 0,
                    "chat": {"id": 1001, "type": "private"},
                    "text": "notification",
                },
            }
        }
    )
    assert (await dialog(driver))[1]["message_id"] == original["message_id"]
    await driver.press(1001, "Остаться")
    assert (await dialog(driver))[1]["title"] == "Untouched draft"
    await driver.bot.session.close()


async def test_start_opens_miniapp_without_command_suggestions(factory, settings):
    settings = settings.model_copy(update={"webhook_base_url": "https://taskflow.example"})
    driver = BotDriver(factory, settings)
    app = create_app(settings, bot=driver.bot, session_factory=factory, workers=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/ready")).status_code == 200
        await driver.text(1001, "/start")
        launcher = next(
            c.reply_markup
            for c in reversed(driver.api.calls)
            if isinstance(c, SendMessage) and isinstance(c.reply_markup, InlineKeyboardMarkup)
        )
        assert launcher.inline_keyboard[0][0].web_app.url == "https://taskflow.example/miniapp"
        assert any(
            isinstance(getattr(c, "reply_markup", None), ReplyKeyboardRemove)
            for c in driver.api.calls
        )
    assert not any(isinstance(c, SetMyCommands) for c in driver.api.calls)


@pytest.mark.parametrize(
    "sender,chat,kind", [(9999, 9999, "private"), (1001, -100, "group"), (1001, 1002, "private")]
)
async def test_unauthorized_never_gets_keyboard_commands_or_dialog(
    factory, settings, sender, chat, kind
):
    driver = BotDriver(factory, settings)
    await driver.put(
        {
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": chat, "type": kind},
                "from": {"id": sender, "is_bot": False, "first_name": "Guest"},
                "text": "/start",
            }
        }
    )
    assert not any(isinstance(c, SetMyCommands) for c in driver.api.calls)
    assert not any(
        isinstance(getattr(c, "reply_markup", None), ReplyKeyboardMarkup) for c in driver.api.calls
    )
    async with factory() as session:
        assert await session.scalar(select(func.count(Dialog.key))) == 0
    await driver.bot.session.close()


async def test_latest_destination_replaces_prompt_without_nesting_or_setup_spam(factory, settings):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    await driver.text(1001, "/templates")
    first = (await dialog(driver))[1]["nav_pending"]
    await driver.text(1001, "🏆 Результаты")
    second = (await dialog(driver))[1]["nav_pending"]
    assert second["data"] == first["data"] and second["nonce"] != first["nonce"]
    await driver.press(1001, "Уйти и удалить")
    assert "Разница" in driver.screen(1001).text
    assert (await dialog(driver))[0] is None
    carriers = [
        c
        for c in driver.api.calls
        if isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardMarkup)
    ]
    assert not carriers
    await driver.bot.session.close()


@pytest.mark.parametrize(
    "corruption", ["nonce", "snapshot", "snapshot_markup", "live_markup", "scalar"]
)
async def test_corrupt_persisted_prompt_recovers_without_mutation(factory, settings, corruption):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    await driver.text(1001, "/tasks")
    async with factory.begin() as session:
        row = await session.scalar(select(Dialog))
        data = deepcopy(row.data)
        if corruption == "nonce":
            data["nav_pending"]["nonce"] = "broken"
        elif corruption == "snapshot":
            data["nav_pending"]["data"]["flow"] = "broken"
        elif corruption == "snapshot_markup":
            data["nav_pending"]["data"]["screen_markup"] = []
        elif corruption == "live_markup":
            data["screen_markup"] = []
        else:
            data["nav_pending"] = 5
        row.data = data
    driver.dp = create_dispatcher(settings)
    await driver.text(1001, "/results")
    assert "НЕДЕЛЯ" in driver.screen(1001).text
    assert (await dialog(driver))[0] is None
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
    await driver.bot.session.close()


async def test_rerating_input_and_confirmation_restore_then_submit_actual_price(factory, settings):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-draft")
    await driver.press(1001, "✅ Создать")
    await driver.text(1002, "/menu")
    await driver.text(1002, "/inbox")
    await driver.press(1002, "500")
    await driver.text(1002, "/tasks")
    await driver.press(1002, "Макс")
    await driver.press(1002, "Untouched")
    await driver.press(1002, "Изменить цену")
    _, original = await dialog(driver, 1002)
    await driver.text(1002, "➕ Задача")
    await driver.press(1002, "Остаться")
    assert (await dialog(driver, 1002))[1]["task_version"] == original["task_version"]
    await driver.text(1002, "800")
    _, confirmation = await dialog(driver, 1002)
    old = data_for(driver, 1002, "✅ Подтвердить")
    await driver.text(1002, "/results")
    await driver.press(1002, "Остаться")
    assert (await dialog(driver, 1002))[1]["confirmation"] == confirmation["confirmation"]
    await driver.press(1002, data=old)
    async with factory() as session:
        assert (await session.scalar(select(Task))).rated_points == 500
    await driver.press(1002, "✅ Подтвердить")
    async with factory() as session:
        assert (await session.scalar(select(Task))).rated_points == 800
    await driver.bot.session.close()


async def test_menu_reopens_at_bottom_then_other_commands_edit_latest_screen(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/tasks")
    _, first = await dialog(driver)
    current_id = first["message_id"]
    for text in ("🏠 Меню", "/menu", "/menu"):
        await driver.text(1001, text)
        next_id = (await dialog(driver))[1]["message_id"]
        assert next_id != current_id
        current_id = next_id
    for text in ("📥 Входящие", "⚡ Шаблоны", "🏆 Результаты"):
        await driver.text(1001, text)
        assert (await dialog(driver))[1]["message_id"] == current_id
    carriers = [
        c
        for c in driver.api.calls
        if isinstance(c, SendMessage) and isinstance(c.reply_markup, ReplyKeyboardMarkup)
    ]
    inline = [
        c
        for c in driver.api.calls
        if isinstance(c, SendMessage) and isinstance(c.reply_markup, InlineKeyboardMarkup)
    ]
    assert not carriers and len(inline) == 4
    await driver.bot.session.close()


async def test_present_null_pending_on_valid_persisted_title_recovers_dashboard(factory, settings):
    driver = BotDriver(factory, settings)
    await enter(driver, "task-title")
    async with factory.begin() as session:
        row = await session.scalar(select(Dialog))
        row.data = {**row.data, "nav_pending": None}
    driver.dp = create_dispatcher(settings)
    await driver.text(1001, "/results")
    assert "НЕДЕЛЯ" in driver.screen(1001).text
    state, data = await dialog(driver)
    assert state is None and "nav_pending" not in data
    async with factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(select(func.count(ScoreEvent.id))) == 0
    await driver.bot.session.close()
