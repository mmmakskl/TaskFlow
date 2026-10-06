from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.ack import answer_callback
from app.bot.callbacks.actions import Action
from app.bot.handlers.common import confirm_action, confirmed_action, form, show, show_type, step
from app.bot.handlers.settings_prompts import TIMEZONES, setting_prompt, type_prompt
from app.bot.keyboards.common import button, keyboard
from app.bot.render import discard_input, render, require_current, reset, safe
from app.bot.states.forms import SettingsForm
from app.models import User
from app.services.penalties import PenaltyService
from app.services.rules import DomainError, text_field
from app.services.settings import SettingsService
from app.services.ui import UIService


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
    await show_type(ui, actor, kind.id, bot, state)


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
        await state.update_data(types_page=action.entity)
        await show(await ui.penalty_types(action.entity, True), bot, actor.telegram_id, state)
    elif action.name == "type_view":
        await reset(state)
        await show_type(ui, actor, action.entity, bot, state)
    elif action.name == "type_new":
        await form(state)
        await type_prompt(bot, actor, state, "name")
    elif action.name == "type_edit":
        if action.value not in ("name", "emoji", "description", "rate", "calculation_type"):
            raise DomainError("Недопустимое поле.")
        await form(state, type_id=action.entity, type_version=action.version, field=action.value)
        if action.value == "calculation_type":
            await type_prompt(bot, actor, state, "method")
        else:
            flow = await step(state, SettingsForm.type_edit)
            labels = {
                "name": "название",
                "emoji": "emoji",
                "description": "описание",
                "rate": "ставку в баллах",
            }
            rows = []
            if action.value == "description":
                rows.append([button("🧹 Убрать описание", "type_clear", version=flow)])
            await render(
                bot,
                actor.telegram_id,
                state,
                f"✏️ Изменить {labels[action.value]}\n\nВведи новое значение:",
                keyboard(*rows, [button("⬅️ К типу штрафа", "type_view", action.entity)]),
            )
    elif action.name == "type_method":
        await require_current(state, SettingsForm.type_method.state, action.version)
        if action.value not in ("PER_MINUTE", "FIXED", "MANUAL"):
            raise DomainError("Недопустимый способ расчёта.")
        await state.update_data(calculation_type=action.value)
        if action.value == "MANUAL":
            await state.update_data(rate=0)
            await save_kind(bot, actor, state, penalty_service, ui)
        else:
            await type_prompt(bot, actor, state, "rate")
    elif action.name == "type_toggle":
        kind = await penalty_service.kind(action.entity)
        if kind.version != action.version:
            raise DomainError("Тип штрафа изменился. Обнови его экран.")
        if not kind.is_active:
            await penalty_service.configure(actor, kind.id, action.version, is_active=True)
            await reset(state)
            await show_type(ui, actor, kind.id, bot, state)
            await answer_callback(query)
            return
        confirm = await confirm_action(state, "type_toggle_confirm", action.entity, action.version)
        await render(
            bot,
            actor.telegram_id,
            state,
            f"⚠️ Выключить {safe(kind.name)}?\n\nНовые штрафы этого типа будут недоступны.\nСтарые штрафы сохранят свои баллы.",
            keyboard(
                [("✅ Подтвердить", confirm.pack())],
                [button("❌ Отмена", "type_view", action.entity)],
            ),
        )
    elif action.name == "type_toggle_confirm":
        await confirmed_action(state, action)
        kind = await penalty_service.kind(action.entity)
        await penalty_service.configure(
            actor, kind.id, action.version, is_active=not kind.is_active
        )
        await reset(state)
        await show_type(ui, actor, kind.id, bot, state)
    else:
        if action.value not in ("timezone", "weekly_target"):
            raise DomainError("Недопустимая настройка.")
        await form(state, field=action.value)
        await setting_prompt(bot, actor, state, action.value)
    await answer_callback(
        query,
    )


async def settings_draft_callback(
    query: CallbackQuery,
    callback_data: Action,
    bot: Bot,
    state: FSMContext,
    actor: User,
    penalty_service: PenaltyService,
    settings_service: SettingsService,
    ui: UIService,
) -> None:
    action = callback_data
    current = await state.get_state()
    if action.name == "setting_value":
        data = await require_current(state, SettingsForm.target.state, action.version)
        field = data["field"]
        if field == "timezone":
            if action.value not in TIMEZONES:
                raise DomainError("Недопустимый часовой пояс.")
            value = TIMEZONES[action.value][1]
        else:
            value = action.value
        await settings_service.change(actor, field, value)
        await reset(state)
        await show(await ui.settings_screen(actor), bot, actor.telegram_id, state)
    elif action.name == "type_clear":
        data = await require_current(state, SettingsForm.type_edit.state, action.version)
        if data.get("field") != "description":
            raise DomainError("Эта кнопка устарела.")
        kind = await penalty_service.configure(
            actor, data["type_id"], data["type_version"], description=""
        )
        await reset(state)
        await show_type(ui, actor, kind.id, bot, state)
    elif action.name == "type_rate_value":
        await require_current(state, SettingsForm.type_rate.state, action.version)
        if not action.value.isdecimal() or len(action.value) > 7:
            raise DomainError("Недопустимая ставка.")
        await state.update_data(rate=int(action.value))
        await save_kind(bot, actor, state, penalty_service, ui)
    elif action.name in ("type_skip", "type_keep"):
        expected = {
            "name": SettingsForm.type_name.state,
            "emoji": SettingsForm.type_emoji.state,
            "description": SettingsForm.type_description.state,
        }
        field = "description" if action.name == "type_skip" else action.value
        if field not in expected:
            raise DomainError("Эта кнопка устарела.")
        data = await require_current(state, expected[field], action.version)
        if action.name == "type_skip":
            await state.update_data(description="")
        elif not data.get(field):
            raise DomainError("Сначала введи значение.")
        await type_prompt(
            bot,
            actor,
            state,
            {"name": "emoji", "emoji": "description", "description": "method"}[field],
        )
    else:
        previous = {
            SettingsForm.type_emoji.state: "name",
            SettingsForm.type_description.state: "emoji",
            SettingsForm.type_method.state: "description",
            SettingsForm.type_rate.state: "method",
        }
        if current not in previous:
            raise DomainError("Эта кнопка устарела.")
        data = await require_current(state, current, action.version)
        if data.get("type_id") and current == SettingsForm.type_method.state:
            await reset(state)
            await show_type(ui, actor, data["type_id"], bot, state)
        else:
            await type_prompt(bot, actor, state, previous[current])
    await answer_callback(query)


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
        await show(await ui.settings_screen(actor), bot, actor.telegram_id, state)
    elif current == SettingsForm.type_name.state:
        await state.update_data(name=text_field(text, 80, "Название"))
        await type_prompt(bot, actor, state, "emoji")
    elif current == SettingsForm.type_emoji.state:
        await state.update_data(emoji=text_field(text, 16, "Emoji"))
        await type_prompt(bot, actor, state, "description")
    elif current == SettingsForm.type_description.state:
        await state.update_data(
            description="" if text == "—" else text_field(text, 500, "Описание")
        )
        await type_prompt(bot, actor, state, "method")
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
        await show_type(ui, actor, kind.id, bot, state)
    await discard_input(message)


def create_router() -> Router:
    created = Router()
    created.callback_query.register(
        settings_draft_callback,
        Action.filter(
            F.name.in_(
                {
                    "settings_back",
                    "setting_value",
                    "type_rate_value",
                    "type_skip",
                    "type_keep",
                    "type_clear",
                }
            )
        ),
    )
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
