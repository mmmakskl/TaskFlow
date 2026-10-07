from aiogram import Bot
from aiogram.fsm.context import FSMContext

from app.bot.handlers.common import show, step
from app.bot.keyboards.common import button, keyboard
from app.bot.keyboards.forms import controls, minute_choices
from app.bot.render import render, safe
from app.bot.states.forms import PenaltyForm
from app.models import User
from app.services.penalties import PenaltyService
from app.services.ui import UIService, points


async def target_prompt(bot: Bot, actor: User, state: FSMContext, service: PenaltyService) -> None:
    flow = await step(state, PenaltyForm.target)
    await render(
        bot,
        actor.telegram_id,
        state,
        "⚠️ <b>Штраф</b>\n\nКому?",
        keyboard(
            [
                button(user.display_name, "penalty_target", user.id, flow)
                for user in await service.users()
            ],
            [button("⬅️ Меню", "menu")],
        ),
    )


async def kind_prompt(
    bot: Bot, actor: User, state: FSMContext, ui: UIService, page: int = 0
) -> None:
    flow = await step(state, PenaltyForm.kind)
    text, markup = await ui.penalty_types(page, flow=flow)
    data = await state.get_data()
    if data.get("deadline_comment"):
        users = {user.id: user for user in await ui.penalties.users()}
        target = users.get(data.get("target_id", 0))
        text = (
            f"👤 Штраф для: {safe(target.display_name if target else 'Участник')}\n"
            f"{safe(data['deadline_comment'])}\n\n" + text
        )
    await show((text, markup), bot, actor.telegram_id, state)


async def quantity_prompt(
    bot: Bot, actor: User, state: FSMContext, service: PenaltyService
) -> None:
    data = await state.get_data()
    kind = await service.kind(data["type_id"])
    flow = await step(state, PenaltyForm.quantity)
    if kind.calculation_type == "PER_MINUTE":
        text = f"{safe(kind.emoji)} <b>{safe(kind.name)}</b> • {points(kind.rate)} баллов/мин\n\n⏱ Введи количество минут (1–10 000) или выбери ниже:"
        rows = minute_choices("penalty_value", flow, (5, 15, 30, 60, 90, 120))
    else:
        text = f"{safe(kind.emoji)} <b>{safe(kind.name)}</b>\n\n💸 Введи размер штрафа в баллах (1–1 000 000 000) или выбери ниже:"
        rows = [
            [
                button(points(value), "penalty_value", version=flow, value=str(value))
                for value in (100, 500, 1000)
            ]
        ]
    await render(
        bot, actor.telegram_id, state, text, keyboard(*rows, controls("penalty_back", flow))
    )


async def comment_choice(bot: Bot, actor: User, state: FSMContext) -> None:
    data = await state.get_data()
    flow = await step(state, PenaltyForm.comment_choice)
    rows = []
    text = "💬 <b>Комментарий к штрафу</b>\n\nНапиши пояснение сюда или пропусти этот шаг."
    if data.get("comment"):
        text += f"\n\nСейчас: {safe(data['comment'])}"
        rows.append([button("Оставить комментарий", "penalty_comment_keep", version=flow)])
    rows += [
        [button("Пропустить", "penalty_comment_skip", version=flow)],
        [button("✏️ Комментарий", "penalty_comment", version=flow)],
        controls("penalty_back", flow),
    ]
    await render(bot, actor.telegram_id, state, text, keyboard(*rows))
