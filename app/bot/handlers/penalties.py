from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import confirm_action, confirmed_action, form, show, step
from app.bot.handlers.penalty_prompts import (
    comment_choice,
    kind_prompt,
    quantity_prompt,
    target_prompt,
)
from app.bot.keyboards.common import button, keyboard
from app.bot.keyboards.forms import controls
from app.bot.render import discard_input, render, require_current, reset, safe
from app.bot.states.forms import PenaltyForm
from app.models import User
from app.services.deadlines import deadline_delay, delay_label
from app.services.penalties import PenaltyService
from app.services.rules import DomainError, text_field
from app.services.tasks import TaskService
from app.services.ui import UIService, deadline, points
from app.utils.time import utcnow


async def confirmation(bot: Bot, actor: User, state: FSMContext, service: PenaltyService) -> None:
    data = await state.get_data()
    preview = data["preview"]
    users = {user.id: user for user in await service.users()}
    target = users.get(data["target_id"])
    if target is None:
        raise DomainError("Этот пользователь недоступен.")
    quantity = f"\n⏱ {preview['quantity']} минут" if preview["quantity"] else ""
    text = f"⚠️ <b>Подтвердить штраф?</b>\n\n👤 {safe(target.display_name)}\n{safe(preview['name'])}{quantity}\n💸 −{points(preview['points'])} баллов"
    if data.get("comment"):
        text += f"\n\n💬 {safe(data['comment'])}"
    flow = await step(state, PenaltyForm.confirm)
    await render(
        bot,
        actor.telegram_id,
        state,
        text,
        keyboard(
            [button("✅ Назначить", "penalty_commit", version=flow, value=data["key"])],
            controls("penalty_back", flow),
        ),
    )


async def penalty_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    penalty_service: PenaltyService,
    ui: UIService,
    task_service: TaskService,
) -> None:
    action = callback_data
    if action.name == "task_penalty":
        task = await task_service.deadline_penalty_task(actor, action.entity, action.version)
        delay = deadline_delay(task, utcnow())
        assert delay is not None
        context = (
            f"Просрочка задачи #{task.id}: {task.title}\n"
            f"Дедлайн: {deadline(task, actor)}\n"
            f"{'Опоздание при выполнении' if task.status == 'DONE' else 'Просрочка при открытии формы'}: {delay_label(delay)}"
        )
        await form(state, deadline_comment=context, comment=context, target_id=task.owner_user_id)
        if task.kind == "SHARED":
            await target_prompt(bot, actor, state, penalty_service)
        else:
            await kind_prompt(bot, actor, state, ui)
    elif action.name == "penalty":
        await form(state)
        await target_prompt(bot, actor, state, penalty_service)
    elif action.name == "penalty_target":
        await require_current(state, PenaltyForm.target.state, action.version)
        user_ids = {user.id for user in await penalty_service.users()}
        if action.entity not in user_ids:
            raise DomainError("Этот пользователь недоступен.")
        data = await state.get_data()
        await state.update_data(
            target_id=action.entity, comment=data.get("deadline_comment"), preview=None
        )
        await kind_prompt(bot, actor, state, ui)
    elif action.name == "penalty_kinds":
        data = await require_current(state, PenaltyForm.kind.state)
        if action.value != str(data["flow"]):
            raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
        await kind_prompt(bot, actor, state, ui, action.entity)
    elif action.name == "penalty_type":
        data = await require_current(state, PenaltyForm.kind.state)
        if action.value != str(data["flow"]):
            raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
        kind = await penalty_service.kind(action.entity)
        if kind.version != action.version or not kind.is_active:
            raise DomainError("Этот тип штрафа изменился. Обнови список.")
        await state.update_data(
            type_id=kind.id,
            type_version=kind.version,
            calculation_type=kind.calculation_type,
            comment=data.get("deadline_comment"),
            preview=None,
        )
        if kind.calculation_type == "FIXED":
            await state.update_data(preview=await penalty_service.preview(kind.id, None, None))
            await comment_choice(bot, actor, state)
        else:
            await quantity_prompt(bot, actor, state, penalty_service)
    elif action.name in ("penalty_comment", "penalty_comment_skip", "penalty_comment_keep"):
        await require_current(state, PenaltyForm.comment_choice.state, action.version)
        if action.name == "penalty_comment":
            flow = await step(state, PenaltyForm.comment)
            await render(
                bot,
                actor.telegram_id,
                state,
                "💬 Напиши комментарий (до 1500 символов):",
                keyboard(controls("penalty_back", flow)),
            )
        else:
            if action.name == "penalty_comment_skip":
                await state.update_data(comment=None)
            await confirmation(bot, actor, state, penalty_service)
    elif action.name == "penalty_commit":
        data = await require_current(state, PenaltyForm.confirm.state, action.version)
        if action.value != data["key"]:
            raise DomainError("Эта кнопка устарела.")
        penalty = await penalty_service.create(
            actor, data["target_id"], data["preview"], data["key"], True, data.get("comment")
        )
        await reset(state)
        await render(
            bot,
            actor.telegram_id,
            state,
            f"✅ Штраф назначен\n\n{safe(penalty.name_snapshot)}\n💸 −{points(penalty.points)} баллов",
            keyboard(
                [button("🏆 Результаты", "results")],
                [button("⚠️ Ещё штраф", "penalty"), button("🏠 Меню", "menu")],
            ),
        )
    elif action.name == "penalty_list":
        await reset(state)
        await state.update_data(penalty_page=action.entity)
        await show(await ui.penalty_list(actor, action.entity), bot, actor.telegram_id, state)
    elif action.name == "penalty_delete":
        penalty = await penalty_service.view(actor, action.entity, action.version)
        target_name = next(
            (
                user.display_name
                for user in await penalty_service.users()
                if user.id == penalty.target_user_id
            ),
            "Участник",
        )
        confirm = await confirm_action(
            state, "penalty_delete_confirm", action.entity, action.version
        )
        data = await state.get_data()
        await render(
            bot,
            actor.telegram_id,
            state,
            f"⚠️ Удалить штраф?\n\n👤 {safe(target_name)}\n{safe(penalty.name_snapshot)} • {points(penalty.points)} баллов\nБаллы вернутся в исходной неделе.",
            keyboard(
                [("✅ Удалить", confirm.pack())],
                [button("❌ Отмена", "penalty_list", data.get("penalty_page", 0))],
            ),
        )
    else:
        await confirmed_action(state, action)
        data = await state.get_data()
        await penalty_service.delete(actor, action.entity, action.version, True)
        await reset(state)
        await show(
            await ui.penalty_list(actor, data.get("penalty_page", 0)), bot, actor.telegram_id, state
        )
    await answer_callback(
        query,
    )


async def accept_quantity(
    bot: Bot, actor: User, state: FSMContext, service: PenaltyService, value: int
) -> None:
    data = await state.get_data()
    preview = await service.preview(
        data["type_id"],
        value if data["calculation_type"] == "PER_MINUTE" else None,
        value if data["calculation_type"] == "MANUAL" else None,
    )
    if preview["type_version"] != data["type_version"]:
        raise DomainError("Тип штрафа изменился. Открой создание штрафа заново.")
    await state.update_data(preview=preview)
    await comment_choice(bot, actor, state)


async def penalty_draft_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    penalty_service: PenaltyService,
    ui: UIService,
) -> None:
    action = callback_data
    current = await state.get_state()
    if action.name == "penalty_value":
        await require_current(state, PenaltyForm.quantity.state, action.version)
        if not action.value.isdecimal() or len(action.value) > 10:
            raise DomainError("Введи положительное целое число.")
        await accept_quantity(bot, actor, state, penalty_service, int(action.value))
    else:
        if current not in {
            PenaltyForm.kind.state,
            PenaltyForm.quantity.state,
            PenaltyForm.comment_choice.state,
            PenaltyForm.comment.state,
            PenaltyForm.confirm.state,
        }:
            raise DomainError("Эта кнопка устарела. Обнови меню.")
        data = await require_current(state, current, action.version)
        if current == PenaltyForm.kind.state:
            await target_prompt(bot, actor, state, penalty_service)
        elif current == PenaltyForm.quantity.state or (
            current == PenaltyForm.comment_choice.state and data["calculation_type"] == "FIXED"
        ):
            await kind_prompt(bot, actor, state, ui)
        elif current == PenaltyForm.comment_choice.state:
            await quantity_prompt(bot, actor, state, penalty_service)
        else:
            await comment_choice(bot, actor, state)
    await answer_callback(query)


async def penalty_quantity(
    message: Message, bot: Bot, state: FSMContext, actor: User, penalty_service: PenaltyService
) -> None:
    text = (message.text or "").strip()
    if not text.isdecimal() or len(text) > 10:
        raise DomainError("Введи положительное целое число.")
    await accept_quantity(bot, actor, state, penalty_service, int(text))
    await discard_input(message)


async def penalty_comment(
    message: Message, bot: Bot, state: FSMContext, actor: User, penalty_service: PenaltyService
) -> None:
    await state.update_data(comment=text_field(message.text or "", 1500, "Комментарий"))
    await confirmation(bot, actor, state, penalty_service)
    await discard_input(message)


def create_router() -> Router:
    created = Router()
    created.callback_query.register(
        penalty_draft_callback, Action.filter(F.name.in_({"penalty_back", "penalty_value"}))
    )
    created.callback_query.register(
        penalty_callback,
        Action.filter(
            F.name.in_(
                {
                    "penalty",
                    "task_penalty",
                    "penalty_target",
                    "penalty_kinds",
                    "penalty_type",
                    "penalty_comment",
                    "penalty_comment_skip",
                    "penalty_comment_keep",
                    "penalty_commit",
                    "penalty_list",
                    "penalty_delete",
                    "penalty_delete_confirm",
                }
            )
        ),
    )
    created.message.register(penalty_quantity, PenaltyForm.quantity)
    created.message.register(
        penalty_comment, StateFilter(PenaltyForm.comment, PenaltyForm.comment_choice)
    )
    return created
