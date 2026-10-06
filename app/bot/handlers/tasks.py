from contextlib import suppress
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import form, show, step
from app.bot.handlers.task_prompts import (
    comment_prompt,
    deadline_prompt,
    estimate_prompt,
    title_prompt,
)
from app.bot.keyboards.common import button, inline_calendar, keyboard
from app.bot.keyboards.forms import controls
from app.bot.render import render, require_current, reset, safe
from app.bot.states.forms import RatingForm, TaskForm
from app.models import User
from app.services.rules import DomainError, estimated_minutes, text_field
from app.services.tasks import TaskService
from app.services.ui import UIService
from app.utils.duration import duration
from app.utils.time import utcnow


async def save_task(
    bot: Bot, actor: User, state: FSMContext, service: TaskService, comment: str | None
) -> None:
    data = await state.get_data()
    deadline = datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None
    task = await service.create(
        actor, data["title"], data["key"], comment, deadline, data.get("estimated_minutes")
    )
    await reset(state)
    await render(
        bot,
        actor.telegram_id,
        state,
        f"✅ <b>Задача создана</b>\n\n📝 {safe(task.title)}\n👤 {safe(actor.display_name)}\n⭐ ждёт оценки второго пользователя\n📅 {'Без дедлайна' if not deadline else deadline.strftime('%d.%m.%Y')}\n⏱ План: {('~' + duration(task.estimated_minutes)) if task.estimated_minutes else 'не указан'}",
        keyboard(
            [button("📋 К задачам", "list", value="active")],
            [button("➕ Ещё задачу", "new"), button("🏠 Меню", "menu")],
        ),
    )


async def new_task(query: CallbackQuery, bot: Bot, state: FSMContext, actor: User) -> None:
    await form(state)
    await answer_callback(query)
    await title_prompt(bot, actor, state)


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
            flow = await step(state, TaskForm.deadline)
            markup = inline_calendar(month.year, month.month, flow)
        except ValueError:
            raise DomainError("Недопустимая дата.") from None
        await render(bot, actor.telegram_id, state, "📅 Выбери дату дедлайна:", markup)
    elif action.name == "deadline":
        today = utcnow().astimezone(ZoneInfo(actor.timezone)).date()
        if action.value == "none":
            selected = None
        elif action.value == "keep" and data.get("deadline"):
            selected = datetime.fromisoformat(data["deadline"])
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
            await estimate_prompt(bot, actor, state)
    elif action.name == "comment_add":
        flow = await step(state, TaskForm.comment)
        await render(
            bot,
            actor.telegram_id,
            state,
            "💬 Напиши комментарий (до 1500 символов):",
            keyboard(controls("task_back", flow, "comment")),
        )
    else:
        await save_task(bot, actor, state, task_service, None)
    await answer_callback(
        query,
    )


async def accept_estimate(
    bot: Bot, actor: User, state: FSMContext, service: TaskService, ui: UIService, value: object
) -> None:
    estimate = estimated_minutes(value)
    data = await state.get_data()
    if data.get("edit_id"):
        await service.edit(actor, data["edit_id"], data["edit_version"], estimated_minutes=estimate)
        await reset(state)
        await show(await ui.task_details(actor, data["edit_id"]), bot, actor.telegram_id, state)
    else:
        await state.update_data(estimated_minutes=estimate)
        await comment_prompt(bot, actor, state)


async def estimate_input(
    message: Message,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    text = (message.text or "").strip()
    if not text.isdecimal() or len(text) > 5:
        raise DomainError("Введи длительность в минутах: целое число от 1 до 10 080.")
    await accept_estimate(bot, actor, state, task_service, ui, int(text))
    with suppress(TelegramBadRequest):
        await message.delete()


async def task_draft_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    action = callback_data
    current = await state.get_state()
    if action.name == "task_estimate":
        data = await require_current(state, TaskForm.estimate.state, action.version)
        if action.value == "none":
            value = None
        elif action.value == "keep":
            value = data.get("estimated_minutes")
        elif action.value.isdecimal() and len(action.value) <= 5:
            value = int(action.value)
        else:
            raise DomainError("Недопустимая длительность.")
        await accept_estimate(bot, actor, state, task_service, ui, value)
    elif action.name == "task_title_keep":
        data = await require_current(state, TaskForm.title.state, action.version)
        if not data.get("title"):
            raise DomainError("Сначала напиши название.")
        await deadline_prompt(bot, actor, state)
    else:
        allowed = {
            TaskForm.deadline.state: {"title", "deadline", "edit"},
            TaskForm.estimate.state: {"deadline", "edit"},
            TaskForm.comment_choice.state: {"estimate"},
            TaskForm.comment.state: {"comment"},
        }
        if action.value not in allowed.get(current, set()):
            raise DomainError("Эта кнопка устарела. Обнови меню.")
        data = await require_current(state, current, action.version)
        if action.value == "edit":
            if not data.get("edit_id"):
                raise DomainError("Эта кнопка устарела.")
            await task_actions(
                query,
                Action(name="edit", entity=data["edit_id"], version=data["edit_version"]),
                bot,
                state,
                actor,
                task_service,
                ui,
            )
            return
        if data.get("edit_id") and action.value != "deadline":
            raise DomainError("Эта кнопка устарела.")
        prompts = {
            "title": title_prompt,
            "deadline": deadline_prompt,
            "estimate": estimate_prompt,
            "comment": comment_prompt,
        }
        await prompts[action.value](bot, actor, state)
    await answer_callback(query)


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
            keyboard([button("⬅️ К оценке", "ratings", task.id - 1), button("🏠 Меню", "menu")]),
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
        if task.owner_user_id != actor.id or task.status in ("DONE", "CANCELLED"):
            raise DomainError("Редактировать можно только свою активную задачу.")
        if task.version != action.version:
            raise DomainError("Эта кнопка устарела. Обнови задачу.")
        await reset(state)
        await render(
            bot,
            actor.telegram_id,
            state,
            "✏️ Что изменить?",
            keyboard(
                [button("Название", "edit_field", task.id, task.version, "title")],
                [button("Комментарий", "edit_field", task.id, task.version, "comment")],
                [button("Дедлайн", "edit_field", task.id, task.version, "deadline")],
                [
                    button(
                        "⏱ Оценка времени", "edit_field", task.id, task.version, "estimated_minutes"
                    )
                ],
                [button("⬅️ К задаче", "task", task.id)],
            ),
        )
    else:
        if action.value not in ("title", "comment", "deadline", "estimated_minutes"):
            raise DomainError("Недопустимое поле.")
        task = await task_service.view(actor, action.entity)
        if (
            task.owner_user_id != actor.id
            or task.version != action.version
            or task.status in ("DONE", "CANCELLED")
        ):
            raise DomainError("Редактирование больше недоступно. Обнови задачу.")
        await form(
            state,
            edit_id=action.entity,
            edit_version=action.version,
            field=action.value,
            estimated_minutes=task.estimated_minutes,
            deadline=task.deadline.isoformat() if task.deadline else None,
        )
        if action.value == "deadline":
            await deadline_prompt(bot, actor, state)
        elif action.value == "estimated_minutes":
            await estimate_prompt(bot, actor, state)
        else:
            await state.set_state(TaskForm.edit)
            text = (
                "✏️ Напиши новое название:"
                if action.value == "title"
                else "💬 Напиши комментарий. Отправь «—», чтобы убрать его:"
            )
            await render(
                bot,
                actor.telegram_id,
                state,
                text,
                keyboard(
                    [
                        button("⬅️ Назад", "edit", task.id, task.version),
                        button("❌ Отмена", "task", task.id),
                    ]
                ),
            )
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
    created.message.register(estimate_input, TaskForm.estimate)
    created.callback_query.register(
        task_draft_callback,
        Action.filter(F.name.in_({"task_back", "task_estimate", "task_title_keep"})),
    )
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
