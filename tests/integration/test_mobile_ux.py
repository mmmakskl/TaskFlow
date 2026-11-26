import re
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    SendMessage,
)
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import func, select

from app.bot.callbacks.actions import callback
from app.bot.dispatcher import create_dispatcher
from app.models import Penalty, PenaltyType, ScoreEvent, Task
from app.models.entities import Dialog
from app.repositories.base import Repository
from app.repositories.tasks import TaskRepository
from app.services.penalties import PenaltyService
from app.services.tasks import TaskService
from tests.integration.test_form_navigation import data_for
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


def buttons(driver, user=1001):
    return [button for row in driver.screen(user).reply_markup.inline_keyboard for button in row]


async def seed_tasks(factory, settings, count=12, done=False):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        ids = []
        for i in range(count):
            task = await service.create(owner, f"Задача {i:02}", f"ux-seed-{i}")
            await service.rate(peer, task.id, 500, task.version)
            if done:
                await service.change_status(owner, task.id, "DONE", task.version)
            ids.append(task.id)
        return ids


async def test_fast_create_comment_input_and_dashboard_single_message(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    assert "Неделя" in driver.screen(1001).text
    assert all(
        any(label in button.text for button in buttons(driver))
        for label in ("Новая задача", "Штраф", "Мои задачи", "Входящие", "Результаты", "Настройки")
    )
    await driver.press(1001, "Новая задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Быстрая задача")
    old = await driver.press(1001, "✅ Создать")
    await driver.press(1001, data=old)
    assert "Задача создана" in driver.screen(1001).text
    await driver.press(1001, "Ещё задачу")
    await driver.press(1001, "Себе")
    await driver.text(1001, "С деталями")
    await driver.press(1001, "Дедлайн")
    await driver.press(1001, "Завтра")
    await driver.press(1001, "⏱ Время")
    await driver.press(1001, "30 мин")
    await driver.press(1001, "Комментарий")
    await driver.text(1001, "Комментарий без лишнего tap")
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        tasks = list(await session.scalars(select(Task).order_by(Task.id)))
        assert len(tasks) == 2
        assert tasks[0].deadline is tasks[0].estimated_minutes is tasks[0].comment is None
        assert (
            tasks[1].comment == "Комментарий без лишнего tap" and tasks[1].estimated_minutes == 30
        )
    assert (
        len(
            [
                call
                for call in driver.api.calls
                if isinstance(call, SendMessage)
                and isinstance(call.reply_markup, InlineKeyboardMarkup)
            ]
        )
        == 1
    )


async def test_filter_page_context_survives_status_edit_and_confirmation(factory, settings):
    await seed_tasks(factory, settings, 12, done=True)
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Мои задачи")
    await driver.press(1001, "Выполненные")
    await driver.press(1001, "▶️")
    assert any(button.text == "2/3" for button in buttons(driver))
    assert any(button.text == "✓ Выполненные" for button in buttons(driver))
    await driver.press(1001, "Задача 06")
    await driver.press(1001, "К выполнению")
    cancelled = data_for(driver, 1001, "✅ Подтвердить")
    await driver.press(1001, "Отмена")
    await driver.press(1001, data=cancelled)
    assert "Выполнено" in driver.screen(1001).text
    async with factory() as session:
        assert all(task.status == "DONE" for task in await session.scalars(select(Task)))
    await driver.press(1001, "К выполнению")
    await driver.press(1001, "Подтвердить")
    await driver.press(1001, "Изменить")
    await driver.press(1001, "Название")
    await driver.text(1001, "Переименована")
    await driver.press(1001, "⬅️ Назад")
    assert any(button.text == "2/3" for button in buttons(driver))
    assert any(button.text == "✓ Выполненные" for button in buttons(driver))


@pytest.mark.parametrize(
    "step",
    ["title", "deadline", "calendar", "estimate", "comment", "penalty", "confirmation", "settings"],
)
async def test_menu_interrupts_forms_and_old_buttons_cannot_resume(factory, settings, step):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    if step == "settings":
        await driver.press(1001, "Настройки")
        await driver.press(1001, "Часовой пояс")
    elif step in ("penalty", "confirmation"):
        await driver.press(1001, "Штраф")
        await driver.press(1001, "Макс")
        await driver.press(1001, "Instagram")
        if step == "confirmation":
            await driver.press(1001, "5 мин")
            await driver.press(1001, "Пропустить")
    else:
        await driver.press(1001, "Новая задача")
        await driver.press(1001, "Себе")
        if step != "title":
            await driver.text(1001, "Interrupted")
        if step in ("deadline", "calendar"):
            await driver.press(1001, "Дедлайн")
        if step == "calendar":
            await driver.press(1001, "Выбрать дату")
        elif step in ("estimate", "comment"):
            await driver.press(1001, "⏱ Время")
            if step == "comment":
                await driver.press(1001, "30 мин")
                await driver.press(1001, "Комментарий")
    old = next(
        (
            button.callback_data
            for button in buttons(driver)
            if not button.callback_data.startswith("a:menu:")
            and not button.callback_data.startswith("a:noop:")
        ),
        callback("task_estimate", version=1, value="30"),
    )
    await driver.text(1001, "/menu")
    assert "Неделя" in driver.screen(1001).text
    await driver.press(1001, data=old)
    assert "Неделя" in driver.screen(1001).text
    async with factory() as session:
        dialog = await session.scalar(select(Dialog).where(Dialog.key.like("%:1001:1001:%")))
        assert dialog.state is None and "key" not in dialog.data
        assert await session.scalar(select(func.count(Task.id))) == 0
        assert await session.scalar(select(func.count(Penalty.id))) == 0


async def test_unexpected_text_commands_and_media_keep_draft_and_buttons(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Новая задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "/unknown")
    assert "Новая задача" in driver.screen(1001).text
    await driver.text(1001, "Saved title")
    await driver.text(1001, "Unexpected choice text")
    assert (
        "Черновик" in driver.screen(1001).text and "Черновик сохранён" in driver.screen(1001).text
    )
    await driver.put(
        {
            "message": {
                "message_id": 50,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 1001, "type": "private"},
                "from": {"id": 1001, "is_bot": False, "first_name": "Test"},
                "photo": [
                    {"file_id": "test", "file_unique_id": "test", "width": 100, "height": 100}
                ],
            }
        }
    )
    assert "Здесь нужен текст" in driver.screen(1001).text
    await driver.press(1001, "✅ Создать")
    await driver.text(1001, "Idle text")
    await driver.text(1001, "Another idle text")
    assert "Неделя" in driver.screen(1001).text
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.title == "Saved title"
    assert (
        len(
            [
                call
                for call in driver.api.calls
                if isinstance(call, SendMessage)
                and isinstance(call.reply_markup, InlineKeyboardMarkup)
            ]
        )
        == 1
    )


async def test_old_mutation_button_cannot_interrupt_unrelated_form(factory, settings):
    await seed_tasks(factory, settings, 1)
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Мои задачи")
    await driver.press(1001, "Задача 00")
    old = data_for(driver, 1001, "✅ Выполнено")
    await driver.press(1001, "Меню")
    await driver.press(1001, data=old)
    assert "Неделя" in driver.screen(1001).text
    await driver.press(1001, "Новая задача")
    await driver.press(1001, "Себе")
    await driver.press(1001, data=old)
    assert "Новая задача" in driver.screen(1001).text
    await driver.text(1001, "Second title")
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        first = await session.scalar(select(Task).order_by(Task.id))
        assert first.status == "TODO" and first.awarded_points is None


async def test_custom_rating_and_skip_advance_from_selected_task(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner = (await repo.users())[0]
        service = TaskService(repo, settings)
        for i in range(3):
            await service.create(owner, f"Оценить {i}", f"rating-{i}")
    driver = BotDriver(factory, settings)
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    await driver.press(1002, "Пропустить")
    assert "Оценить 1" in driver.screen(1002).text
    await driver.press(1002, "Другое")
    await driver.text(1002, "oops")
    assert "К оценке" in str(driver.screen(1002).reply_markup)
    await driver.text(1002, "725")
    assert "Оценить 2" in driver.screen(1002).text
    await driver.press(1002, "1000")
    assert "Оценить 0" in driver.screen(1002).text
    await driver.press(1002, "250")
    assert "Всё оценено" in driver.screen(1002).text
    async with factory() as session:
        assert [
            task.rated_points for task in await session.scalars(select(Task).order_by(Task.id))
        ] == [250, 725, 1000]


@pytest.mark.parametrize("action", ["penalty_delete", "type_toggle"])
async def test_cancelled_destructive_confirmation_stays_invalid(factory, settings, action):
    async with factory.begin() as session:
        repo = Repository(session)
        owner = (await repo.users())[0]
        service = PenaltyService(repo, settings)
        kind = await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        await service.create(
            owner, owner.id, await service.preview(kind.id, 5, None), "confirmation", True
        )
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    if action == "penalty_delete":
        await driver.press(1001, "Результаты")
        await driver.press(1001, "Все штрафы")
        await driver.press(1001, "🗑")
        old = data_for(driver, 1001, "✅ Удалить")
    else:
        await driver.press(1001, "Настройки")
        await driver.press(1001, "Типы штрафов")
        await driver.press(1001, "Instagram")
        await driver.press(1001, "Выключить")
        old = data_for(driver, 1001, "✅ Подтвердить")
    await driver.press(1001, "Отмена")
    await driver.press(1001, data=old)
    async with factory() as session:
        assert (await session.scalar(select(Penalty))).deleted_at is None
        assert (
            await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        ).is_active
    await driver.press(1001, "🗑" if action == "penalty_delete" else "Выключить")
    await driver.press(1001, "✅ Удалить" if action == "penalty_delete" else "Подтвердить")
    async with factory() as session:
        if action == "penalty_delete":
            assert (await session.scalar(select(Penalty))).deleted_at is not None
            assert await session.scalar(select(func.sum(ScoreEvent.points))) == 0
        else:
            assert not (
                await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
            ).is_active


async def test_menu_reopens_at_chat_bottom(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.text(1001, "/menu")
    await driver.text(1001, "/menu")
    assert "Неделя" in driver.screen(1001).text
    assert (
        len(
            [
                call
                for call in driver.api.calls
                if isinstance(call, SendMessage)
                and isinstance(call.reply_markup, InlineKeyboardMarkup)
            ]
        )
        == 3
    )
    assert not any(isinstance(call, EditMessageText) for call in driver.api.calls)


@pytest.mark.parametrize(
    "corruption",
    [
        {"state": "UnknownForm:lost", "data": {}},
        {"state": "TaskForm:estimate", "data": {"flow": 1, "key": "test"}},
        {"state": "PenaltyForm:confirm", "data": {"flow": 1, "key": "test"}},
    ],
)
async def test_incomplete_fsm_restores_menu_without_traceback(factory, settings, corruption):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    async with factory.begin() as session:
        dialog = await session.scalar(select(Dialog))
        dialog.state = corruption["state"]
        dialog.data = {**dialog.data, **corruption["data"]}
    await driver.text(1001, "text")
    assert "Черновик сброшен" in driver.screen(1001).text
    assert "Неделя" in driver.screen(1001).text


async def test_form_survives_dispatcher_restart_and_malformed_callback(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Новая задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Persistent draft")
    driver.dp = create_dispatcher(settings)
    await driver.press(1001, data="unknown:callback:format")
    assert "актуальные кнопки" in driver.screen(1001).text
    await driver.press(1001, "Дедлайн")
    await driver.press(1001, "Завтра")
    await driver.press(1001, "⏱ Время")
    await driver.press(1001, "15 мин")
    await driver.press(1001, "✅ Создать")
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert task.title == "Persistent draft" and task.estimated_minutes == 15


async def test_maximum_escaped_content_fits_telegram_limits(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        task = await TaskService(repo, settings).create(
            owner, "😀" * 200, "long", comment="<&>" * 500, estimate=10080
        )
        task_id = task.id
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, data=callback("task", task_id))
    assert "&lt;&amp;&gt;" in driver.screen(1001).text
    await driver.text(1002, "/menu")
    await driver.press(1002, "Входящие")
    for user in (1001, 1002):
        text = driver.screen(user).text
        plain = unescape(re.sub(r"<[^>]*>", "", text))
        assert len(plain.encode("utf-16-le")) // 2 <= 4096
        assert all(len(button.callback_data.encode()) <= 64 for button in buttons(driver, user))
    await driver.press(1002, "500")
    assert not any(
        isinstance(call, AnswerCallbackQuery) and call.show_alert for call in driver.api.calls
    )


async def test_enabling_type_is_one_tap_and_type_page_survives_edit(factory, settings):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Типы штрафов")
    await driver.press(1001, "▶️")
    await driver.press(1001, "Другое")
    assert "Вручную при назначении" in driver.screen(1001).text
    assert "MANUAL" not in driver.screen(1001).text
    await driver.press(1001, "Выключить")
    await driver.press(1001, "Подтвердить")
    await driver.press(1001, "Включить")
    assert "✅ Включён" in driver.screen(1001).text
    await driver.press(1001, "Название")
    await driver.text(1001, "Новый заголовок")
    await driver.press(1001, "Типы штрафов")
    assert any(button.text == "• 2 •" for button in buttons(driver))
    assert any("Новый заголовок" in button.text for button in buttons(driver))


async def test_results_history_filters_cancel_and_old_confirmation(factory, settings):
    from app.utils.time import utcnow

    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner, peer = await repo.users()
        service = TaskService(repo, settings)
        task = await service.create(owner, "Отменить", "cancel")
        await service.rate(peer, task.id, 800, task.version)
        session.add(
            ScoreEvent(
                user_id=owner.id,
                category="task",
                points=1200,
                effective_at=utcnow() - timedelta(days=7),
                operation_key="past-week",
            )
        )
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Мои задачи")
    await driver.press(1001, "Активные")
    await driver.press(1001, "Отменить")
    await driver.press(1001, "Отменить")
    old = data_for(driver, 1001, "✅ Подтвердить")
    await driver.press(1001, "Отмена")
    await driver.press(1001, data=old)
    assert "К выполнению" in driver.screen(1001).text
    await driver.press(1001, "Отменить")
    await driver.press(1001, "Подтвердить")
    assert "Отменено" in driver.screen(1001).text
    await driver.press(1001, "⬅️ Назад")
    assert "Здесь пока нет задач" in driver.screen(1001).text
    await driver.press(1001, "Архив")
    assert any("Отменить" in button.text for button in buttons(driver))
    await driver.press(1001, "Меню")
    await driver.press(1001, "Результаты")
    await driver.press(1001, "Прошлая неделя")
    assert "1 200" in driver.screen(1001).text
    await driver.press(1001, "История")
    assert "1 200" in driver.screen(1001).text
    await driver.press(1001, "Результаты")
    assert "Задачи: +0" in driver.screen(1001).text


async def test_optional_comment_and_description_clear_with_one_button(factory, settings):
    async with factory.begin() as session:
        repo = TaskRepository(session)
        owner = (await repo.users())[0]
        await TaskService(repo, settings).create(
            owner, "Очистить комментарий", "clear", comment="Комментарий"
        )
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    await driver.press(1001, "Мои задачи")
    await driver.press(1001, "Очистить")
    await driver.press(1001, "Изменить")
    await driver.press(1001, "Комментарий")
    old = await driver.press(1001, "Убрать комментарий")
    await driver.press(1001, data=old)
    assert "Комментарий:" not in driver.screen(1001).text
    await driver.press(1001, "Меню")
    await driver.press(1001, "Настройки")
    await driver.press(1001, "Типы штрафов")
    await driver.press(1001, "Instagram")
    await driver.press(1001, "Описание")
    await driver.press(1001, "Убрать описание")
    async with factory() as session:
        assert (await session.scalar(select(Task))).comment is None
        assert (
            await session.scalar(select(PenaltyType).where(PenaltyType.seed_key == "instagram"))
        ).description == ""


async def test_optional_message_cleanup_network_failure_does_not_undo_action(factory, settings):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import DeleteMessage

    driver = BotDriver(factory, settings)
    original = driver.api.make_request

    async def request(bot, method, timeout=None):  # noqa: ASYNC109 - Bot API signature
        if isinstance(method, DeleteMessage):
            raise TelegramNetworkError(method=method, message="injected network failure")
        return await original(bot, method, timeout)

    driver.api.make_request = request
    await driver.text(1001, "/menu")
    await driver.press(1001, "Новая задача")
    await driver.press(1001, "Себе")
    await driver.text(1001, "Still saved")
    await driver.press(1001, "⏱ Время")
    await driver.press(1001, "30 мин")
    await driver.press(1001, "Комментарий")
    await driver.text(1001, "Comment saved despite cleanup failure")
    await driver.press(1001, "✅ Создать")
    assert "Задача создана" in driver.screen(1001).text
    async with factory() as session:
        task = await session.scalar(select(Task))
        assert (
            task.title == "Still saved" and task.comment == "Comment saved despite cleanup failure"
        )


async def test_notification_opens_task_inside_chat_and_preserves_dialog(factory, settings):
    from app.notifications.worker import send_notification

    await seed_tasks(factory, settings, 1)
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/menu")
    async with factory() as session:
        original = (await session.scalar(select(Dialog))).data["message_id"]
    # Seeding rated the task and queued a notification; deliver it to Max's mock chat.
    for _ in range(5):
        notifications = [
            call
            for call in driver.api.calls
            if isinstance(call, SendMessage)
            and call.chat_id == 1001
            and "задача оценена" in call.text
        ]
        if notifications:
            break
        await send_notification(factory, driver.bot, settings)
    else:
        pytest.fail("Rating-completed notification was not delivered")
    control = notifications[-1].reply_markup.inline_keyboard[0][0]
    assert control.web_app is None and control.url is None
    assert control.callback_data.startswith("a:task:")
    async with factory() as session:
        dialog = await session.scalar(select(Dialog))
        assert dialog.data["message_id"] == original
    await driver.press(1001, "Открыть задачу")
    await driver.press(1001, "Начать")
    assert "В работе" in driver.screen(1001).text
