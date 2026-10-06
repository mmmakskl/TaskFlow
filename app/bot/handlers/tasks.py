from contextlib import suppress
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import form, show
from app.bot.keyboards.common import button, cancel, inline_calendar, keyboard
from app.bot.render import render, require_current, reset, safe
from app.bot.states.forms import RatingForm, TaskForm
from app.models import User
from app.services.rules import DomainError, text_field
from app.services.tasks import TaskService
from app.services.ui import UIService
from app.utils.time import utcnow


async def deadline_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    data = await state.get_data()
    flow = data["flow"]
    await state.set_state(TaskForm.deadline)
    await render(
        bot,
        actor.telegram_id,
        state,
        "📅 Добавить дедлайн?",
        keyboard(
            [button("Без дедлайна", "deadline", version=flow, value="none")],
            [
                button("Сегодня", "deadline", version=flow, value="today"),
                button("Завтра", "deadline", version=flow, value="tomorrow"),
            ],
            [button("Выбрать дату", "calendar", version=flow)],
            [button("❌ Отмена", "menu")],
        ),
    )


async def save_task(
    bot: Bot, actor: User, state: FSMContext, service: TaskService, comment: str | None
) -> None:
    data = await state.get_data()
    deadline = datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None
    task = await service.create(actor, data["title"], data["key"], comment, deadline)
    await reset(state)
    await render(
        bot,
        actor.telegram_id,
        state,
        f"✅ <b>Задача создана</b>\n\n📝 {safe(task.title)}\n👤 {safe(actor.display_name)}\n⭐ ждёт оценки второго пользователя\n📅 {'Без дедлайна' if not deadline else deadline.strftime('%d.%m.%Y')}",
        keyboard(
            [button("📋 К задачам", "list", value="active")],
            [button("➕ Ещё задачу", "new"), button("🏠 Меню", "menu")],
        ),
    )


async def new_task(query: CallbackQuery, bot: Bot, state: FSMContext, actor: User) -> None:
    await form(state)
    await state.set_state(TaskForm.title)
    await answer_callback(
        query,
    )
    await render(
        bot, actor.telegram_id, state, "📝 <b>Новая задача</b>\n\nНапиши название задачи:", cancel()
    )


async def title_input(message: Message, bot: Bot, state: FSMContext, actor: User) -> None:
    title = text_field(message.text or "", 200, "Название")
    await state.update_data(title=title)
    with suppress(TelegramBadRequest):
        await message.delete()
    await deadline_prompt(bot, actor, state)


async def task_form_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    action = callback_data
    expected = (
        TaskForm.deadline.state
        if action.name in ("calendar", "deadline")
        else TaskForm.comment_choice.state
    )
    data = await require_current(state, expected, action.version)
    if action.name == "calendar":
        today = utcnow().astimezone(ZoneInfo(actor.timezone)).date()
        try:
            month = date.fromisoformat(action.value) if action.value else today
            markup = inline_calendar(month.year, month.month, data["flow"])
        except ValueError:
            raise DomainError("Недопустимая дата.") from None
        await render(bot, actor.telegram_id, state, "📅 Выбери дату дедлайна:", markup)
    elif action.name == "deadline":
        today = utcnow().astimezone(ZoneInfo(actor.timezone)).date()
        if action.value == "none":
            selected = None
        else:
            try:
                day = (
                    today + timedelta(days=action.value == "tomorrow")
                    if action.value in ("today", "tomorrow")
                    else date.fromisoformat(action.value)
                )
            except ValueError:
                raise DomainError("Недопустимая дата.") from None
            if day < today or day.year > 2100:
                raise DomainError("Выбери сегодняшнюю или будущую дату.")
            selected = datetime.combine(day, time(23, 59, 59), ZoneInfo(actor.timezone))
        if data.get("edit_id"):
            await task_service.edit(actor, data["edit_id"], data["edit_version"], deadline=selected)
            await reset(state)
            await show(await ui.task_details(actor, data["edit_id"]), bot, actor.telegram_id, state)
        else:
            await state.update_data(deadline=selected.isoformat() if selected else None)
            await state.set_state(TaskForm.comment_choice)
            await render(
                bot,
                actor.telegram_id,
                state,
                "💬 Добавить комментарий?",
                keyboard(
                    [button("Пропустить", "comment_skip", version=data["flow"])],
                    [button("✏️ Добавить комментарий", "comment_add", version=data["flow"])],
                    [button("❌ Отмена", "menu")],
                ),
            )
    elif action.name == "comment_add":
        await state.set_state(TaskForm.comment)
        await render(
            bot, actor.telegram_id, state, "💬 Напиши комментарий (до 1500 символов):", cancel()
        )
    else:
        await save_task(bot, actor, state, task_service, None)
    await answer_callback(
        query,
    )


async def comment_input(
    message: Message, bot: Bot, state: FSMContext, actor: User, task_service: TaskService
) -> None:
    comment = text_field(message.text or "", 1500, "Комментарий")
    await save_task(bot, actor, state, task_service, comment)
    with suppress(TelegramBadRequest):
        await message.delete()


async def task_actions(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    action = callback_data
    if action.name == "rate":
        try:
            value = int(action.value)
        except ValueError:
            raise DomainError("Недопустимая оценка.") from None
        await task_service.rate(actor, action.entity, value, action.version)
        await reset(state)
        await show(await ui.rating(actor), bot, actor.telegram_id, state)
    elif action.name == "custom_rate":
        task = await task_service.view(actor, action.entity)
        if (
            task.evaluator_user_id != actor.id
            or task.version != action.version
            or task.status != "BACKLOG"
        ):
            raise DomainError("Эта задача больше не ждёт твоей оценки.")
        await form(state, task_id=task.id, task_version=task.version)
        await state.set_state(RatingForm.points)
        await render(
            bot,
            actor.telegram_id,
            state,
            f"⭐ {safe(task.title)}\n\nВведи целое число от 1 до 10 000:",
            cancel(),
        )
    elif action.name in ("status", "status_confirm"):
        task = await task_service.view(actor, action.entity)
        if task.version != action.version:
            raise DomainError("Эта кнопка устарела. Обнови задачу.")
        if action.name == "status" and (task.status == "DONE" or action.value == "CANCELLED"):
            await render(
                bot,
                actor.telegram_id,
                state,
                "⚠️ Подтвердить изменение задачи?\nЭто действие может изменить результаты недели.",
                keyboard(
                    [
                        button(
                            "✅ Подтвердить", "status_confirm", task.id, task.version, action.value
                        )
                    ],
                    [button("❌ Отмена", "task", task.id)],
                ),
            )
        else:
            await task_service.change_status(
                actor, task.id, action.value, action.version, action.name == "status_confirm"
            )
            await show(await ui.task_details(actor, task.id), bot, actor.telegram_id, state)
    elif action.name == "edit":
        task = await task_service.view(actor, action.entity)
        await render(
            bot,
            actor.telegram_id,
            state,
            "✏️ Что изменить?",
            keyboard(
                [button("Название", "edit_field", task.id, task.version, "title")],
                [button("Комментарий", "edit_field", task.id, task.version, "comment")],
                [button("Дедлайн", "edit_field", task.id, task.version, "deadline")],
                [button("⬅️ К задаче", "task", task.id)],
            ),
        )
    else:
        if action.value not in ("title", "comment", "deadline"):
            raise DomainError("Недопустимое поле.")
        await form(state, edit_id=action.entity, edit_version=action.version, field=action.value)
        if action.value == "deadline":
            await deadline_prompt(bot, actor, state)
        else:
            await state.set_state(TaskForm.edit)
            text = (
                "✏️ Напиши новое название:"
                if action.value == "title"
                else "💬 Напиши комментарий. Отправь «—», чтобы убрать его:"
            )
            await render(bot, actor.telegram_id, state, text, cancel())
    await answer_callback(
        query,
    )


async def custom_rating(
    message: Message,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    text = (message.text or "").strip()
    if not text.isdecimal() or len(text) > 5:
        raise DomainError("Введи целое число от 1 до 10 000.")
    data = await state.get_data()
    await task_service.rate(actor, data["task_id"], int(text), data["task_version"])
    await reset(state)
    await show(await ui.rating(actor), bot, actor.telegram_id, state)
    with suppress(TelegramBadRequest):
        await message.delete()


async def edit_input(
    message: Message,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    data = await state.get_data()
    field = data["field"]
    value = message.text or ""
    change = None if field == "comment" and value.strip() == "—" else value
    await task_service.edit(actor, data["edit_id"], data["edit_version"], **{field: change})
    await reset(state)
    await show(await ui.task_details(actor, data["edit_id"]), bot, actor.telegram_id, state)
    with suppress(TelegramBadRequest):
        await message.delete()


def create_router() -> Router:
    created = Router()
    created.callback_query.register(new_task, Action.filter(F.name == "new"))
    created.message.register(title_input, TaskForm.title)
    created.callback_query.register(
        task_form_callback,
        Action.filter(F.name.in_({"calendar", "deadline", "comment_add", "comment_skip"})),
    )
    created.message.register(comment_input, TaskForm.comment)
    created.callback_query.register(
        task_actions,
        Action.filter(
            F.name.in_({"rate", "custom_rate", "status", "status_confirm", "edit", "edit_field"})
        ),
    )
    created.message.register(custom_rating, RatingForm.points)
    created.message.register(edit_input, TaskForm.edit)
    return created
