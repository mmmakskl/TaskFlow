from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import form, show
from app.bot.keyboards.common import button, cancel, keyboard
from app.bot.render import render, require_current, reset, safe
from app.bot.states.forms import PenaltyForm
from app.models import User
from app.services.penalties import PenaltyService
from app.services.rules import DomainError, text_field
from app.services.ui import UIService, points


async def comment_choice(bot: Bot, actor: User, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(PenaltyForm.comment_choice)
    await render(
        bot,
        actor.telegram_id,
        state,
        "💬 Добавить комментарий к штрафу?",
        keyboard(
            [button("Пропустить", "penalty_comment_skip", version=data["flow"])],
            [button("✏️ Комментарий", "penalty_comment", version=data["flow"])],
            [button("❌ Отмена", "menu")],
        ),
    )


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
    await state.set_state(PenaltyForm.confirm)
    await render(
        bot,
        actor.telegram_id,
        state,
        text,
        keyboard(
            [button("✅ Назначить", "penalty_commit", version=data["flow"], value=data["key"])],
            [button("❌ Отмена", "menu")],
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
) -> None:
    action = callback_data
    if action.name == "penalty":
        flow = await form(state)
        await state.set_state(PenaltyForm.target)
        users = await penalty_service.users()
        await render(
            bot,
            actor.telegram_id,
            state,
            "⚠️ <b>Штраф</b>\n\nКому?",
            keyboard(
                [button(user.display_name, "penalty_target", user.id, flow) for user in users],
                [button("❌ Отмена", "menu")],
            ),
        )
    elif action.name == "penalty_target":
        await require_current(state, PenaltyForm.target.state, action.version)
        user_ids = {user.id for user in await penalty_service.users()}
        if action.entity not in user_ids:
            raise DomainError("Этот пользователь недоступен.")
        await state.update_data(target_id=action.entity)
        await state.set_state(PenaltyForm.kind)
        screen = await ui.penalty_types(flow=action.version)
        await show(screen, bot, actor.telegram_id, state)
    elif action.name == "penalty_kinds":
        data = await require_current(state, PenaltyForm.kind.state)
        if action.value != str(data["flow"]):
            raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
        await show(
            await ui.penalty_types(action.entity, flow=data["flow"]), bot, actor.telegram_id, state
        )
    elif action.name == "penalty_type":
        data = await require_current(state, PenaltyForm.kind.state)
        if action.value != str(data["flow"]):
            raise DomainError("Эта кнопка относится к другой форме. Обнови меню.")
        kind = await penalty_service.kind(action.entity)
        if kind.version != action.version or not kind.is_active:
            raise DomainError("Этот тип штрафа изменился. Обнови список.")
        await state.update_data(
            type_id=kind.id, type_version=kind.version, calculation_type=kind.calculation_type
        )
        if kind.calculation_type == "FIXED":
            await state.update_data(preview=await penalty_service.preview(kind.id, None, None))
            await comment_choice(bot, actor, state)
        else:
            await state.set_state(PenaltyForm.quantity)
            text = (
                "⏱ Введи количество минут (1–10 000):"
                if kind.calculation_type == "PER_MINUTE"
                else "💸 Введи размер штрафа в баллах (1–1 000 000 000):"
            )
            await render(bot, actor.telegram_id, state, text, cancel())
    elif action.name in ("penalty_comment", "penalty_comment_skip"):
        await require_current(state, PenaltyForm.comment_choice.state, action.version)
        if action.name == "penalty_comment":
            await state.set_state(PenaltyForm.comment)
            await render(
                bot, actor.telegram_id, state, "💬 Напиши комментарий (до 1500 символов):", cancel()
            )
        else:
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
        await show(await ui.penalty_list(actor, action.entity), bot, actor.telegram_id, state)
    elif action.name == "penalty_delete":
        await render(
            bot,
            actor.telegram_id,
            state,
            "⚠️ Удалить штраф?\nБаллы вернутся в исходной неделе.",
            keyboard(
                [button("✅ Удалить", "penalty_delete_confirm", action.entity, action.version)],
                [button("❌ Отмена", "penalty_list")],
            ),
        )
    else:
        await penalty_service.delete(actor, action.entity, action.version, True)
        await reset(state)
        await show(await ui.penalty_list(actor, 0), bot, actor.telegram_id, state)
    await answer_callback(
        query,
    )


async def penalty_quantity(
    message: Message, bot: Bot, state: FSMContext, actor: User, penalty_service: PenaltyService
) -> None:
    text = (message.text or "").strip()
    if not text.isdecimal() or len(text) > 10:
        raise DomainError("Введи положительное целое число.")
    data = await state.get_data()
    value = int(text)
    preview = await penalty_service.preview(
        data["type_id"],
        value if data["calculation_type"] == "PER_MINUTE" else None,
        value if data["calculation_type"] == "MANUAL" else None,
    )
    if preview["type_version"] != data["type_version"]:
        raise DomainError("Тип штрафа изменился. Открой создание штрафа заново.")
    await state.update_data(preview=preview)
    await comment_choice(bot, actor, state)
    with suppress(TelegramBadRequest):
        await message.delete()


async def penalty_comment(
    message: Message, bot: Bot, state: FSMContext, actor: User, penalty_service: PenaltyService
) -> None:
    await state.update_data(comment=text_field(message.text or "", 1500, "Комментарий"))
    await confirmation(bot, actor, state, penalty_service)
    with suppress(TelegramBadRequest):
        await message.delete()


def create_router() -> Router:
    created = Router()
    created.callback_query.register(
        penalty_callback,
        Action.filter(
            F.name.in_(
                {
                    "penalty",
                    "penalty_target",
                    "penalty_kinds",
                    "penalty_type",
                    "penalty_comment",
                    "penalty_comment_skip",
                    "penalty_commit",
                    "penalty_list",
                    "penalty_delete",
                    "penalty_delete_confirm",
                }
            )
        ),
    )
    created.message.register(penalty_quantity, PenaltyForm.quantity)
    created.message.register(penalty_comment, PenaltyForm.comment)
    return created
