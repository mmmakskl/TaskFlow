"""Shared template screens; all mutations delegated to the template service."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import confirm_action, confirmed_action, form, step
from app.bot.handlers.task_prompts import draft_prompt
from app.bot.keyboards.common import button, keyboard, pager
from app.bot.render import discard_input, render, require_current, reset, safe, short
from app.bot.states.forms import TemplateForm
from app.models import User
from app.services.rules import DomainError, estimated_minutes, rating_points, text_field
from app.services.ui import UIService
from app.utils.duration import duration


async def template_list(
    bot: Bot, actor: User, state: FSMContext, ui: UIService, page: int = 0
) -> None:
    count = await ui.templates.count(actor, False)
    page = min(page, max(0, (count - 1) // 5))
    templates = await ui.templates.list(actor, False, page)
    await reset(state)
    await state.update_data(templates_page=page)
    await render(
        bot,
        actor.telegram_id,
        state,
        f"📚 <b>Шаблоны • {count}</b>",
        keyboard(
            *[
                [
                    button(
                        f"{short(t.title, 30)} • {t.points} • {duration(t.estimated_minutes) if t.estimated_minutes else '—'}"
                        + (" • выкл" if not t.is_active else ""),
                        "tpl_view",
                        t.id,
                    )
                ]
                for t in templates
            ],
            pager("templates", page, (page + 1) * 5 < count),
            [button("➕ Новый шаблон", "tpl_new"), button("🏠 Меню", "menu")],
        ),
    )


async def template_details(
    bot: Bot, actor: User, state: FSMContext, ui: UIService, entity: int
) -> None:
    t = await ui.templates.view(actor, entity)
    await reset(state)
    page = (await state.get_data()).get("templates_page", 0)
    await render(
        bot,
        actor.telegram_id,
        state,
        f"📚 <b>{safe(t.title)}</b>\n⭐ {t.points}\n⏱ {duration(t.estimated_minutes) if t.estimated_minutes else 'не указано'}\n"
        + ("✅ Включён" if t.is_active else "⏸ Выключен"),
        keyboard(
            *([[button("➕ Создать задачу", "tpl_use", t.id, t.version)]] if t.is_active else []),
            [
                button("Название", "tpl_edit", t.id, t.version, "title"),
                button("Баллы", "tpl_edit", t.id, t.version, "points"),
            ],
            [button("Время", "tpl_edit", t.id, t.version, "estimated_minutes")],
            [
                button(
                    "⏸ Выключить" if t.is_active else "✅ Включить", "tpl_toggle", t.id, t.version
                )
            ],
            [button("🗑 Удалить шаблон", "tpl_delete", t.id, t.version)],
            [button("⬅️ Шаблоны", "templates", page)],
        ),
    )


async def field_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    data = await state.get_data()
    flow = await step(state, TemplateForm.field)
    labels = {
        "title": "Название (до 200 символов)",
        "points": "Баллы (1–10 000)",
        "estimated_minutes": "Время в минутах (1–10 080)",
    }
    await render(
        bot,
        actor.telegram_id,
        state,
        "📚 " + labels[data["field"]],
        keyboard(
            *(
                [[button("Не указывать", "tpl_none", version=flow)]]
                if data["field"] == "estimated_minutes"
                else []
            ),
            [
                button("⬅️ Назад", "tpl_back", version=flow),
                button("❌ Отмена", "templates", data.get("templates_page", 0)),
            ],
        ),
    )


async def template_actions(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    ui: UIService,
) -> None:
    a = callback_data
    if a.name == "templates":
        await template_list(bot, actor, state, ui, a.entity)
    elif a.name == "tpl_view":
        await template_details(bot, actor, state, ui, a.entity)
    elif a.name == "tpl_disable_confirm":
        await confirmed_action(state, a)
        await ui.templates.disable(actor, a.entity, a.version)
        await template_details(bot, actor, state, ui, a.entity)
    elif a.name == "tpl_delete_confirm":
        await confirmed_action(state, a)
        await ui.templates.delete(actor, a.entity, a.version)
        await template_list(bot, actor, state, ui)
    elif a.name == "tpl_delete":
        t = await ui.templates.view(actor, a.entity)
        if t.version != a.version:
            raise DomainError("Шаблон изменился. Открой его снова.")
        confirm = await confirm_action(state, "tpl_delete_confirm", t.id, t.version)
        await render(
            bot,
            actor.telegram_id,
            state,
            f"🗑 Удалить шаблон «{safe(t.title)}»? Созданные по нему задачи сохранят название и оценку.",
            keyboard(
                [("🗑 Удалить", confirm.pack())],
                [button("❌ Отмена", "tpl_view", t.id)],
            ),
        )
        await answer_callback(query)
        return
    elif a.name == "tpl_new":
        await form(state, field="title")
        await field_prompt(bot, actor, state)
    elif a.name in ("tpl_use", "tpl_edit", "tpl_toggle"):
        t = await ui.templates.view(actor, a.entity)
        if t.version != a.version:
            raise DomainError("Шаблон изменился. Открой его снова.")
        if a.name == "tpl_use":
            if not t.is_active:
                raise DomainError("Шаблон выключен.")
            snapshot = {
                "title": t.title,
                "proposed_points": t.points,
                "estimated_minutes": t.estimated_minutes,
                "pricing_mode": t.pricing_mode,
                "base_quantity": t.base_quantity,
                "unit_label": t.unit_label,
            }
            await form(
                state,
                **snapshot,
                target="SELF",
                template_id=t.id,
                template_version=t.version,
                template_snapshot=snapshot,
                peer_name=next(
                    user.display_name
                    for user in await ui.tasks.user_repo.participants()
                    if user.id != actor.id
                ),
            )
            await draft_prompt(bot, actor, state)
        elif a.name == "tpl_toggle":
            if t.is_active:
                confirm = await confirm_action(state, "tpl_disable_confirm", t.id, t.version)
                await render(
                    bot,
                    actor.telegram_id,
                    state,
                    f"⏸ Выключить {safe(t.title)}?",
                    keyboard(
                        [("✅ Подтвердить", confirm.pack())],
                        [button("❌ Отмена", "tpl_view", t.id)],
                    ),
                )
                await answer_callback(query)
                return
            else:
                await ui.templates.edit(actor, t.id, t.version, is_active=True)
            await template_details(bot, actor, state, ui, t.id)
        else:
            if a.value not in ("title", "points", "estimated_minutes"):
                raise DomainError("Недопустимое поле.")
            await form(state, template_edit_id=t.id, template_edit_version=t.version, field=a.value)
            await field_prompt(bot, actor, state)
    else:
        data = await require_current(state, TemplateForm.field.state, a.version)
        if a.name == "tpl_none":
            if data["field"] != "estimated_minutes":
                raise DomainError("Недопустимое поле.")
            await accept_field(bot, actor, state, ui, None)
        elif data.get("template_edit_id"):
            await template_details(bot, actor, state, ui, data["template_edit_id"])
        elif data["field"] == "title":
            await template_list(bot, actor, state, ui, data.get("templates_page", 0))
        else:
            await state.update_data(field="title" if data["field"] == "points" else "points")
            await field_prompt(bot, actor, state)
    await answer_callback(query)


async def accept_field(
    bot: Bot, actor: User, state: FSMContext, ui: UIService, value: object
) -> None:
    data = await state.get_data()
    field = data["field"]
    if data.get("template_edit_id"):
        t = await ui.templates.edit(
            actor, data["template_edit_id"], data["template_edit_version"], **{field: value}
        )
        await template_details(bot, actor, state, ui, t.id)
        return
    await state.update_data({field: value})
    data = await state.get_data()
    if field != "estimated_minutes":
        await state.update_data(field="points" if field == "title" else "estimated_minutes")
        await field_prompt(bot, actor, state)
    else:
        t = await ui.templates.create(
            actor, data["title"], data["points"], data["key"], data.get("estimated_minutes")
        )
        await template_details(bot, actor, state, ui, t.id)


async def template_input(
    message: Message, bot: Bot, state: FSMContext, actor: User, ui: UIService
) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    value: object
    if data["field"] == "title":
        value = text_field(text, 200, "Название")
    else:
        if not text.isdecimal() or len(text) > 5:
            raise DomainError("Введи целое число.")
        value = (
            rating_points(int(text)) if data["field"] == "points" else estimated_minutes(int(text))
        )
    await accept_field(bot, actor, state, ui, value)
    await discard_input(message)


def create_router() -> Router:
    router = Router()
    router.callback_query.register(
        template_actions,
        Action.filter(
            F.name.in_(
                {
                    "templates",
                    "tpl_view",
                    "tpl_use",
                    "tpl_new",
                    "tpl_edit",
                    "tpl_toggle",
                    "tpl_disable_confirm",
                    "tpl_delete",
                    "tpl_delete_confirm",
                    "tpl_none",
                    "tpl_back",
                }
            )
        ),
    )
    router.message.register(template_input, TemplateForm.field)
    return router
