from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import form, show
from app.bot.keyboards.common import button, cancel, keyboard
from app.bot.render import render, require_current, reset
from app.bot.states.forms import SettingsForm
from app.models import User
from app.services.penalties import PenaltyService
from app.services.rules import DomainError, text_field
from app.services.settings import SettingsService
from app.services.ui import UIService


async def method_prompt(bot: Bot, actor: User, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(SettingsForm.type_method)
    await render(
        bot,
        actor.telegram_id,
        state,
        "⚙️ Как рассчитывать штраф?",
        keyboard(
            [button("За минуту", "type_method", version=data["flow"], value="PER_MINUTE")],
            [button("Фиксированная сумма", "type_method", version=data["flow"], value="FIXED")],
            [button("Вручную при назначении", "type_method", version=data["flow"], value="MANUAL")],
            [button("❌ Отмена", "menu")],
        ),
    )


async def save_kind(
    bot: Bot, actor: User, state: FSMContext, service: PenaltyService, ui: UIService
) -> None:
    data = await state.get_data()
    if data.get("type_id"):
        changes = {"calculation_type": data["calculation_type"]}
        if data["calculation_type"] != "MANUAL":
            changes["rate"] = data["rate"]
        kind = await service.configure(actor, data["type_id"], data["type_version"], **changes)
    else:
        kind = await service.configure(
            actor,
            None,
            name=data["name"],
            emoji=data["emoji"],
            description=data["description"],
            calculation_type=data["calculation_type"],
            rate=data["rate"],
        )
    await reset(state)
    await show(await ui.type_details(kind.id), bot, actor.telegram_id, state)


async def settings_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    penalty_service: PenaltyService,
    ui: UIService,
) -> None:
    action = callback_data
    if action.name == "types":
        await reset(state)
        await show(await ui.penalty_types(action.entity, True), bot, actor.telegram_id, state)
    elif action.name == "type_view":
        await reset(state)
        await show(await ui.type_details(action.entity), bot, actor.telegram_id, state)
    elif action.name == "type_new":
        await form(state)
        await state.set_state(SettingsForm.type_name)
        await render(
            bot,
            actor.telegram_id,
            state,
            "⚠️ Новый тип штрафа\n\nНапиши название (до 80 символов):",
            cancel(),
        )
    elif action.name == "type_edit":
        if action.value not in ("name", "emoji", "description", "rate", "calculation_type"):
            raise DomainError("Недопустимое поле.")
        await form(state, type_id=action.entity, type_version=action.version, field=action.value)
        if action.value == "calculation_type":
            await method_prompt(bot, actor, state)
        else:
            await state.set_state(SettingsForm.type_edit)
            await render(bot, actor.telegram_id, state, "✏️ Введи новое значение:", cancel())
    elif action.name == "type_method":
        await require_current(state, SettingsForm.type_method.state, action.version)
        if action.value not in ("PER_MINUTE", "FIXED", "MANUAL"):
            raise DomainError("Недопустимый способ расчёта.")
        await state.update_data(calculation_type=action.value)
        if action.value == "MANUAL":
            await state.update_data(rate=0)
            await save_kind(bot, actor, state, penalty_service, ui)
        else:
            await state.set_state(SettingsForm.type_rate)
            await render(
                bot, actor.telegram_id, state, "💰 Введи ставку в баллах (1–1 000 000):", cancel()
            )
    elif action.name == "type_toggle":
        await render(
            bot,
            actor.telegram_id,
            state,
            "⚠️ Изменить доступность типа штрафа?\nСтарые штрафы сохранят свои баллы.",
            keyboard(
                [button("✅ Подтвердить", "type_toggle_confirm", action.entity, action.version)],
                [button("❌ Отмена", "type_view", action.entity)],
            ),
        )
    elif action.name == "type_toggle_confirm":
        kind = await penalty_service.kind(action.entity)
        await penalty_service.configure(
            actor, kind.id, action.version, is_active=not kind.is_active
        )
        await show(await ui.type_details(kind.id), bot, actor.telegram_id, state)
    else:
        if action.value not in ("timezone", "weekly_target"):
            raise DomainError("Недопустимая настройка.")
        await form(state, field=action.value)
        await state.set_state(SettingsForm.target)
        text = (
            "🎯 Введи недельную цель (1–1 000 000):"
            if action.value == "weekly_target"
            else "🌍 Введи часовой пояс для отображения дат, например Europe/Helsinki:"
        )
        await render(bot, actor.telegram_id, state, text, cancel())
    await answer_callback(
        query,
    )


async def settings_input(
    message: Message,
    bot: Bot,
    state: FSMContext,
    actor: User,
    penalty_service: PenaltyService,
    settings_service: SettingsService,
    ui: UIService,
) -> None:
    current = await state.get_state()
    data = await state.get_data()
    text = (message.text or "").strip()
    if current == SettingsForm.target.state:
        await settings_service.change(actor, data["field"], text)
        await reset(state)
        await show(await ui.menu(actor), bot, actor.telegram_id, state)
    elif current == SettingsForm.type_name.state:
        await state.update_data(name=text_field(text, 80, "Название"))
        await state.set_state(SettingsForm.type_emoji)
        await render(bot, actor.telegram_id, state, "Напиши emoji для типа штрафа:", cancel())
    elif current == SettingsForm.type_emoji.state:
        await state.update_data(emoji=text_field(text, 16, "Emoji"))
        await state.set_state(SettingsForm.type_description)
        await render(
            bot,
            actor.telegram_id,
            state,
            "Добавь описание (до 500 символов). Можно отправить «—»:",
            cancel(),
        )
    elif current == SettingsForm.type_description.state:
        await state.update_data(description=text_field(text, 500, "Описание"))
        await method_prompt(bot, actor, state)
    elif current == SettingsForm.type_rate.state:
        if not text.isdecimal() or len(text) > 7:
            raise DomainError("Введи целое число от 1 до 1 000 000.")
        await state.update_data(rate=int(text))
        await save_kind(bot, actor, state, penalty_service, ui)
    else:
        value: object = text
        if data["field"] == "rate":
            if not text.isdecimal() or len(text) > 7:
                raise DomainError("Введи целое число от 1 до 1 000 000.")
            value = int(text)
        kind = await penalty_service.configure(
            actor, data["type_id"], data["type_version"], **{data["field"]: value}
        )
        await reset(state)
        await show(await ui.type_details(kind.id), bot, actor.telegram_id, state)
    with suppress(TelegramBadRequest):
        await message.delete()


def create_router() -> Router:
    created = Router()
    created.callback_query.register(
        settings_callback,
        Action.filter(
            F.name.in_(
                {
                    "types",
                    "type_view",
                    "type_new",
                    "type_edit",
                    "type_method",
                    "type_toggle",
                    "type_toggle_confirm",
                    "user_setting",
                }
            )
        ),
    )
    created.message.register(
        settings_input,
        StateFilter(
            SettingsForm.type_name,
            SettingsForm.type_emoji,
            SettingsForm.type_description,
            SettingsForm.type_rate,
            SettingsForm.type_edit,
            SettingsForm.target,
        ),
    )
    return created
