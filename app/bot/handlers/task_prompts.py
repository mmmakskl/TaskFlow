from aiogram import Bot
from aiogram.fsm.context import FSMContext

from app.bot.handlers.common import step
from app.bot.keyboards.common import button, keyboard
from app.bot.keyboards.forms import controls, minute_choices
from app.bot.render import render, safe
from app.bot.states.forms import TaskForm
from app.models import User
from app.utils.duration import duration


async def title_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.title)
    data = await state.get_data()
    text = "📝 <b>Новая задача</b>\n\nНапиши название задачи (до 200 символов):"
    rows = []
    if data.get("title"):
        text += f"\n\nСейчас: {safe(data['title'])}"
        rows.append([button("Оставить название", "task_title_keep", version=flow)])
    await render(bot, actor.telegram_id, state, text, keyboard(*rows, [button("⬅️ Меню", "menu")]))


async def deadline_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.deadline)
    data = await state.get_data()
    rows = []
    if not data.get("edit_id"):
        rows.append([button("✅ Создать без деталей", "task_save", version=flow)])
    if data.get("deadline"):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        selected = datetime.fromisoformat(data["deadline"]).astimezone(ZoneInfo(actor.timezone))
        rows.append(
            [button(f"Оставить {selected:%d.%m.%Y}", "deadline", version=flow, value="keep")]
        )
    await render(
        bot,
        actor.telegram_id,
        state,
        "📅 <b>Дедлайн</b>\n\nДобавить дату, к которой нужно закончить?",
        keyboard(
            *rows,
            [button("Без дедлайна", "deadline", version=flow, value="none")],
            [
                button("Сегодня", "deadline", version=flow, value="today"),
                button("Завтра", "deadline", version=flow, value="tomorrow"),
            ],
            [button("Выбрать дату", "calendar", version=flow)],
            controls("task_back", flow, "edit" if data.get("edit_id") else "title"),
        ),
    )


async def estimate_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.estimate)
    data = await state.get_data()
    text = "⏱ <b>Сколько времени займёт задача?</b>\n\nВыбери примерную длительность или напиши число минут (1–10 080).\nОценка поможет спланировать день."
    rows = minute_choices("task_estimate", flow, (5, 15, 30, 60, 120, 240))
    if data.get("estimated_minutes"):
        text += f"\n\nСейчас: ~{duration(data['estimated_minutes'])}"
        rows.append([button("Оставить оценку", "task_estimate", version=flow, value="keep")])
    rows.append([button("Не указывать", "task_estimate", version=flow, value="none")])
    rows.append(controls("task_back", flow, "edit" if data.get("edit_id") else "deadline"))
    await render(bot, actor.telegram_id, state, text, keyboard(*rows))


async def comment_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.comment_choice)
    await render(
        bot,
        actor.telegram_id,
        state,
        "💬 <b>Комментарий</b>\n\nНапиши детали сюда или пропусти этот шаг.",
        keyboard(
            [button("Пропустить", "comment_skip", version=flow)],
            [button("✏️ Добавить комментарий", "comment_add", version=flow)],
            controls("task_back", flow, "estimate"),
        ),
    )
