from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import confirm_action, confirmed_action, form, show, show_task, step
from app.bot.handlers.task_prompts import (
    comment_prompt,
    deadline_prompt,
    draft_prompt,
    estimate_prompt,
    points_prompt,
    quantity_prompt,
    target_prompt,
    title_prompt,
)
from app.bot.keyboards.common import button, inline_calendar, keyboard
from app.bot.keyboards.forms import controls
from app.bot.render import discard_input, render, require_current, reset, safe
from app.bot.states.forms import RatingForm, TaskForm
from app.models import TaskTarget, User
from app.services.rules import (
    DomainError,
    StaleAction,
    estimated_minutes,
    rating_points,
    template_points,
    text_field,
)
from app.services.tasks import TaskService
from app.services.ui import UIService
from app.utils.time import utcnow


async def save_task(
    bot: Bot,
    actor: User,
    state: FSMContext,
    service: TaskService,
    comment: str | None,
    ui: UIService,
) -> None:
    data = await state.get_data()
    deadline = datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None
    try:
        task = await service.create(
            actor,
            data["title"],
            data["key"],
            comment,
            deadline,
            data.get("estimated_minutes"),
            target=TaskTarget(data.get("target", "SELF")),
            proposed_points=data.get("proposed_points"),
            template_id=data.get("template_id"),
            template_version=data.get("template_version"),
            template_quantity=data.get("template_quantity"),
        )
    except StaleAction:
        if not data.get("template_id"):
            raise
        template = await ui.templates.view(actor, data["template_id"])
        old = data["template_snapshot"]
        fresh = {
            "title": template.title,
            "proposed_points": template.points,
            "estimated_minutes": template.estimated_minutes,
            "pricing_mode": template.pricing_mode,
            "base_quantity": template.base_quantity,
            "unit_label": template.unit_label,
        }
        changes = {
            field: value for field, value in fresh.items() if data.get(field) == old.get(field)
        }
        await state.update_data(
            changes,
            template_version=template.version if template.is_active else None,
            template_snapshot=fresh,
            template_quantity=None
            if template.pricing_mode != "FIXED"
            else data.get("template_quantity"),
            proposed_points=(
                template.points
                if template.pricing_mode != "FIXED" and data.get("template_id")
                else data.get("proposed_points")
            ),
            template_id=template.id if template.is_active else None,
        )
        await draft_prompt(
            bot,
            actor,
            state,
            "Шаблон обновлён. Проверь черновик и нажми «Создать» снова."
            if template.is_active
            else "Шаблон выключен. Проверь черновик и создай обычное предложение явно.",
        )
        return
    await reset(state)
    await show(await ui.task_created(actor, task), bot, actor.telegram_id, state)


async def new_task(
    query: CallbackQuery, bot: Bot, state: FSMContext, actor: User, ui: UIService
) -> None:
    previous = await state.get_data()
    peer = next(user for user in await ui.tasks.user_repo.participants() if user.id != actor.id)
    shared = previous.get("filter", "").startswith("shared_")
    await form(state, target="SHARED" if shared else "SELF", peer_name=peer.display_name)
    await answer_callback(query)
    await (title_prompt if shared else target_prompt)(bot, actor, state)


async def title_input(message: Message, bot: Bot, state: FSMContext, actor: User) -> None:
    title = text_field(message.text or "", 200, "Название")
    await state.update_data(title=title)
    await discard_input(message)
    await draft_prompt(bot, actor, state)


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
            markup = inline_calendar(month.year, month.month, flow, today)
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
            selected = datetime.combine(day, time.max, ZoneInfo(actor.timezone))
        if data.get("edit_id"):
            await task_service.edit(actor, data["edit_id"], data["edit_version"], deadline=selected)
            await reset(state)
            await show_task(ui, actor, data["edit_id"], bot, state)
        else:
            await state.update_data(deadline=selected.isoformat() if selected else None)
            await draft_prompt(bot, actor, state)
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
        await state.update_data(comment=None)
        await draft_prompt(bot, actor, state)
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
        await show_task(ui, actor, data["edit_id"], bot, state)
    else:
        await state.update_data(estimated_minutes=estimate)
        await draft_prompt(bot, actor, state)


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
    await discard_input(message)


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
    if action.name == "task_save":
        data = await require_current(state, TaskForm.draft.state, action.version)
        await save_task(bot, actor, state, task_service, data.get("comment"), ui)
    elif action.name == "draft_target":
        data = await require_current(state, TaskForm.target.state, action.version)
        if action.value not in ("SELF", "PEER", "SHARED"):
            raise DomainError("Недопустимый исполнитель.")
        await state.update_data(target=action.value)
        await (draft_prompt if data.get("title") else title_prompt)(bot, actor, state)
    elif action.name == "draft_field":
        await require_current(state, TaskForm.draft.state, action.version)
        prompts = {
            "title": title_prompt,
            "target": target_prompt,
            "points": points_prompt,
            "template_quantity": quantity_prompt,
            "deadline": deadline_prompt,
            "estimate": estimate_prompt,
            "comment": comment_prompt,
        }
        if action.value not in prompts:
            raise DomainError("Недопустимое поле.")
        await prompts[action.value](bot, actor, state)
    elif action.name == "draft_template":
        data = await require_current(state, TaskForm.draft.state, action.version)
        await state.update_data(
            save_template=True,
            template_save_key="template:" + data["key"] + ":" + str(action.version),
        )
        if data.get("proposed_points"):
            await save_template(bot, actor, state, ui)
        else:
            await points_prompt(bot, actor, state)
    elif action.name == "draft_points_none":
        await require_current(state, TaskForm.points.state, action.version)
        await state.update_data(proposed_points=None)
        await draft_prompt(bot, actor, state)
    elif action.name == "draft_back":
        await require_current(state, current, action.version)
        await draft_prompt(bot, actor, state)
    elif action.name == "task_estimate":
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
    elif action.name == "task_clear":
        data = await require_current(state, TaskForm.edit.state, action.version)
        if data.get("field") != "comment":
            raise DomainError("Эта кнопка устарела.")
        await task_service.edit(actor, data["edit_id"], data["edit_version"], comment=None)
        await reset(state)
        await show_task(ui, actor, data["edit_id"], bot, state)
    else:
        data = await require_current(state, current, action.version)
        if action.value == "menu":
            await reset(state)
            await show(await ui.menu(actor), bot, actor.telegram_id, state)
            await answer_callback(query)
            return
        if action.value == "draft":
            await state.update_data(save_template=False)
            await draft_prompt(bot, actor, state)
            await answer_callback(query)
            return
        if action.value == "target":
            await target_prompt(bot, actor, state)
            await answer_callback(query)
            return
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
    message: Message,
    bot: Bot,
    state: FSMContext,
    actor: User,
    task_service: TaskService,
    ui: UIService,
) -> None:
    comment = text_field(message.text or "", 1500, "Комментарий")
    await state.update_data(comment=comment)
    await draft_prompt(bot, actor, state)
    await discard_input(message)


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
    if action.name in ("reject", "reject_confirm", "rerate_confirm"):
        if action.name == "reject":
            task = await task_service.view(actor, action.entity)
            if (
                task.version != action.version
                or task.status != "BACKLOG"
                or task.evaluator_user_id != actor.id
            ):
                raise DomainError("Это предложение больше недоступно.")
            confirm = await confirm_action(state, "reject_confirm", task.id, task.version)
            await render(
                bot,
                actor.telegram_id,
                state,
                f"🚫 Отклонить {safe(task.title)}?",
                keyboard(
                    [("✅ Отклонить", confirm.pack())],
                    [button("⬅️ Входящие", "ratings", task.id - 1)],
                ),
            )
        else:
            payload = await confirmed_action(state, action)
            if action.name == "reject_confirm":
                await task_service.reject(actor, action.entity, action.version, confirmed=True)
                await reset(state)
                await show(await ui.rating(actor, action.entity), bot, actor.telegram_id, state)
            else:
                await task_service.rerate(
                    actor, action.entity, payload["points"], action.version, confirmed=True
                )
                await reset(state)
                await show_task(ui, actor, action.entity, bot, state)
    elif action.name == "rate":
        try:
            value = int(action.value)
        except ValueError:
            raise DomainError("Недопустимая оценка.") from None
        await task_service.rate(actor, action.entity, value, action.version)
        await reset(state)
        await show(await ui.rating(actor, action.entity), bot, actor.telegram_id, state)
    elif action.name in ("custom_rate", "rerate"):
        task = await task_service.view(actor, action.entity)
        if (
            task.version != action.version
            or (
                action.name == "custom_rate"
                and (task.evaluator_user_id != actor.id or task.status != "BACKLOG")
            )
            or (
                action.name == "rerate"
                and (
                    task.rated_by_user_id != actor.id or task.status not in ("TODO", "IN_PROGRESS")
                )
            )
        ):
            raise DomainError("Эта задача больше не ждёт твоей оценки.")
        await form(
            state,
            task_id=task.id,
            task_version=task.version,
            rerate=action.name == "rerate",
            old_points=task.rated_points,
        )
        await state.set_state(RatingForm.points)
        await render(
            bot,
            actor.telegram_id,
            state,
            f"⭐ {safe(task.title)}\n\nВведи целое число от 1 до 10 000:",
            keyboard([button("⬅️ К оценке", "ratings", task.id - 1), button("🏠 Меню", "menu")]),
        )
    elif action.name in ("status", "status_confirm"):
        confirmation = (
            await confirmed_action(state, action) if action.name == "status_confirm" else None
        )
        target = confirmation["target"] if confirmation else action.value
        task = await task_service.view(actor, action.entity)
        if task.version != action.version:
            raise DomainError("Эта кнопка устарела. Обнови задачу.")
        if action.name == "status" and (task.status == "DONE" or target == "CANCELLED"):
            confirm = await confirm_action(
                state, "status_confirm", task.id, task.version, target=target
            )
            await render(
                bot,
                actor.telegram_id,
                state,
                ui.status_confirmation(task, target),
                keyboard(
                    [("✅ Подтвердить", confirm.pack())],
                    [button("❌ Отмена", "task", task.id)],
                ),
            )
        else:
            await task_service.change_status(
                actor, task.id, target, action.version, action.name == "status_confirm"
            )
            await reset(state)
            await show_task(ui, actor, task.id, bot, state)
    elif action.name == "edit":
        task = await task_service.view(actor, action.entity)
        if not task_service.can_manage(task, actor) or task.status in ("DONE", "CANCELLED"):
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
            not task_service.can_manage(task, actor)
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
            flow = await step(state, TaskForm.edit)
            text = (
                "✏️ Напиши новое название:"
                if action.value == "title"
                else "💬 Напиши комментарий. Отправь «—», чтобы убрать его:"
            )
            rows = []
            if action.value == "comment":
                text += f"\n\nСейчас: {safe(task.comment or '—')}"
                rows.append([button("🧹 Убрать комментарий", "task_clear", version=flow)])
            await render(
                bot,
                actor.telegram_id,
                state,
                text,
                keyboard(
                    *rows,
                    [
                        button("⬅️ Назад", "edit", task.id, task.version),
                        button("❌ Отмена", "task", task.id),
                    ],
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
    value = rating_points(int(text))
    if data.get("rerate"):
        if value == data["old_points"]:
            await task_service.rerate(actor, data["task_id"], value, data["task_version"])
            await reset(state)
            await show_task(ui, actor, data["task_id"], bot, state)
        else:
            confirm = await confirm_action(
                state, "rerate_confirm", data["task_id"], data["task_version"], points=value
            )
            await render(
                bot,
                actor.telegram_id,
                state,
                f"⭐ Изменить цену: {data['old_points']} → {value}?",
                keyboard(
                    [("✅ Подтвердить", confirm.pack())],
                    [button("❌ Отмена", "task", data["task_id"])],
                ),
            )
    else:
        await task_service.rate(actor, data["task_id"], value, data["task_version"])
        await reset(state)
        await show(await ui.rating(actor, data["task_id"]), bot, actor.telegram_id, state)
    await discard_input(message)


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
    await show_task(ui, actor, data["edit_id"], bot, state)
    await discard_input(message)


async def save_template(bot: Bot, actor: User, state: FSMContext, ui: UIService) -> None:
    data = await state.get_data()
    await ui.templates.create(
        actor,
        data["title"],
        data["proposed_points"],
        data["template_save_key"],
        data.get("estimated_minutes"),
    )
    await state.update_data(save_template=False)
    await draft_prompt(bot, actor, state, "📚 Шаблон сохранён. Черновик задачи сохранён.")


async def template_quantity_input(
    message: Message, bot: Bot, state: FSMContext, actor: User
) -> None:
    text = (message.text or "").strip()
    if not text.isdecimal() or len(text) > 5:
        raise DomainError("Введи целое количество.")
    data = await state.get_data()
    snapshot = data["template_snapshot"]
    value = int(text)
    points = template_points(
        snapshot["pricing_mode"],
        snapshot["proposed_points"],
        snapshot["base_quantity"],
        value,
    )
    await state.update_data(template_quantity=value, proposed_points=points)
    await draft_prompt(bot, actor, state)
    await discard_input(message)


async def points_input(
    message: Message, bot: Bot, state: FSMContext, actor: User, ui: UIService
) -> None:
    text = (message.text or "").strip()
    value = rating_points(int(text)) if text.isdecimal() and len(text) <= 5 else None
    if value is None:
        raise DomainError("Введи целое число от 1 до 10 000.")
    await state.update_data(proposed_points=value)
    if (await state.get_data()).get("save_template"):
        await save_template(bot, actor, state, ui)
    else:
        await draft_prompt(bot, actor, state)
    await discard_input(message)


def create_router() -> Router:
    created = Router()
    created.callback_query.register(new_task, Action.filter(F.name == "new"))
    created.message.register(title_input, TaskForm.title)
    created.message.register(estimate_input, TaskForm.estimate)
    created.message.register(template_quantity_input, TaskForm.quantity)
    created.message.register(points_input, TaskForm.points)
    created.callback_query.register(
        task_draft_callback,
        Action.filter(
            F.name.in_(
                {
                    "task_back",
                    "task_estimate",
                    "task_save",
                    "task_clear",
                    "draft_target",
                    "draft_field",
                    "draft_template",
                    "draft_points_none",
                    "draft_back",
                }
            )
        ),
    )
    created.callback_query.register(
        task_form_callback,
        Action.filter(F.name.in_({"calendar", "deadline", "comment_add", "comment_skip"})),
    )
    created.message.register(comment_input, StateFilter(TaskForm.comment, TaskForm.comment_choice))
    created.callback_query.register(
        task_actions,
        Action.filter(
            F.name.in_(
                {
                    "rate",
                    "custom_rate",
                    "rerate",
                    "rerate_confirm",
                    "reject",
                    "reject_confirm",
                    "status",
                    "status_confirm",
                    "edit",
                    "edit_field",
                }
            )
        ),
    )
    created.message.register(custom_rating, RatingForm.points)
    created.message.register(edit_input, TaskForm.edit)
    return created
