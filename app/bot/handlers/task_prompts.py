from datetime import datetime
from zoneinfo import ZoneInfo

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
        rows.append([button("Оставить название", "draft_back", version=flow)])
    await render(
        bot,
        actor.telegram_id,
        state,
        text,
        keyboard(*rows, controls("task_back", flow, "draft" if data.get("title") else "target")),
    )


async def deadline_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.deadline)
    data = await state.get_data()
    rows = []

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
            controls("task_back", flow, "edit" if data.get("edit_id") else "draft"),
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
    rows.append(controls("task_back", flow, "edit" if data.get("edit_id") else "draft"))
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
            controls("task_back", flow, "draft"),
        ),
    )


TARGET_LABELS = {"SELF": "Себе", "PEER": "Другому", "SHARED": "Вместе • Нам обоим"}


async def target_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.target)
    data = await state.get_data()
    await render(
        bot,
        actor.telegram_id,
        state,
        "👥 Для кого задача?",
        keyboard(
            *[
                [
                    button(
                        ("✓ " if data.get("target", "SELF") == name else "")
                        + (
                            label + " • " + data.get("peer_name", "Другой участник")
                            if name == "PEER"
                            else label
                        ),
                        "draft_target",
                        version=flow,
                        value=name,
                    )
                ]
                for name, label in TARGET_LABELS.items()
            ],
            [button("📚 Из шаблона", "templates")],
            controls("task_back", flow, "draft" if data.get("title") else "menu"),
        ),
    )


async def draft_prompt(bot: Bot, actor: User, state: FSMContext, notice: str = "") -> None:
    flow = await step(state, TaskForm.draft)
    data = await state.get_data()
    deadline_text = (
        datetime.fromisoformat(data["deadline"])
        .astimezone(ZoneInfo(actor.timezone))
        .strftime("%d.%m.%Y")
        if data.get("deadline")
        else "Без дедлайна"
    )
    price_text = (
        str(data["proposed_points"]) + (" каждому" if data.get("target") == "SHARED" else "")
        if data.get("proposed_points")
        else "оценит " + data.get("peer_name", "другой участник")
    )
    target_text = (
        data.get("peer_name", "Другому")
        if data.get("target") == "PEER"
        else TARGET_LABELS[data.get("target", "SELF")]
    )
    text = (notice + "\n\n" if notice else "") + (
        f"📝 <b>Черновик</b>\n\n{safe(data['title'])}\n"
        f"👥 {safe(target_text)}\n"
        f"⭐ Предложение: {safe(price_text)}\n"
        f"📅 {deadline_text}\n"
        f"⏱ План: {duration(data['estimated_minutes']) if data.get('estimated_minutes') else 'не указан'}\n"
        f"💬 {safe(data.get('comment') or '—')}"
    )
    if data.get("template_id"):
        snap = data["template_snapshot"]
        unchanged = all(
            data.get(field) == snap.get(field)
            for field in ("title", "estimated_minutes", "proposed_points")
        )
        text += "\n📚 Шаблон: " + ("готовая оценка" if unchanged else "изменён: нужна оценка")
    await render(
        bot,
        actor.telegram_id,
        state,
        text,
        keyboard(
            [
                button("Название", "draft_field", version=flow, value="title"),
                button("Для кого", "draft_field", version=flow, value="target"),
            ],
            [
                button("Баллы", "draft_field", version=flow, value="points"),
                button("Дедлайн", "draft_field", version=flow, value="deadline"),
            ],
            [
                button("⏱ Время", "draft_field", version=flow, value="estimate"),
                button("Комментарий", "draft_field", version=flow, value="comment"),
            ],
            [button("📚 Сохранить как шаблон", "draft_template", version=flow)],
            [button("✅ Создать", "task_save", version=flow), button("❌ Отмена", "menu")],
        ),
    )


async def points_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    flow = await step(state, TaskForm.points)
    data = await state.get_data()
    text = "⭐ Введи целое число от 1 до 10 000:"
    if data.get("save_template"):
        text += "\nДля шаблона баллы обязательны."
    await render(
        bot,
        actor.telegram_id,
        state,
        text,
        keyboard(
            *(
                []
                if data.get("save_template")
                else [[button("Не указывать", "draft_points_none", version=flow)]]
            ),
            controls("task_back", flow, "draft"),
        ),
    )
