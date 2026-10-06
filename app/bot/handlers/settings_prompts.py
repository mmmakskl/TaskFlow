from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.fsm.context import FSMContext

from app.bot.handlers.common import step
from app.bot.keyboards.common import button, keyboard
from app.bot.keyboards.forms import controls
from app.bot.render import render, safe
from app.bot.states.forms import SettingsForm
from app.models import User
from app.utils.time import utcnow

TIMEZONES = {
    "moscow": ("Москва", "Europe/Moscow"),
    "helsinki": ("Хельсинки", "Europe/Helsinki"),
    "utc": ("UTC", "UTC"),
}


async def setting_prompt(bot: Bot, actor: User, state: FSMContext, field: str) -> None:
    flow = await step(state, SettingsForm.target)
    await state.update_data(field=field)
    if field == "timezone":
        text = f"🌍 <b>Часовой пояс дат</b>\n\nСейчас: {safe(actor.timezone)}\nВыбери город или напиши часовой пояс, например Europe/Moscow.\n\nЭто меняет отображение дат и выбор дедлайна. Общая неделя результатов остаётся прежней."
        rows = []
        for key, (city, zone) in TIMEZONES.items():
            offset = utcnow().astimezone(ZoneInfo(zone)).strftime("%z")
            label = f"{'✓ ' if actor.timezone == zone else ''}{city} • UTC{offset[:3]}:{offset[3:]}"
            rows.append([button(label, "setting_value", version=flow, value=key)])
    else:
        text = f"🎯 <b>Недельная цель</b>\n\nСейчас: {actor.weekly_target:,} баллов\nВыбери цель или напиши целое число от 1 до 1 000 000."
        rows = [
            [
                button(
                    f"{value:,}".replace(",", " "), "setting_value", version=flow, value=str(value)
                )
                for value in values
            ]
            for values in ((5000, 10000), (15000, 20000))
        ]
    rows.append([button("⬅️ Настройки", "settings")])
    await render(bot, actor.telegram_id, state, text, keyboard(*rows))


async def type_prompt(bot: Bot, actor: User, state: FSMContext, target: str) -> None:
    states = {
        "name": SettingsForm.type_name,
        "emoji": SettingsForm.type_emoji,
        "description": SettingsForm.type_description,
        "method": SettingsForm.type_method,
        "rate": SettingsForm.type_rate,
    }
    flow = await step(state, states[target])
    data = await state.get_data()
    if target == "method":
        text = "⚙️ <b>Как рассчитывать штраф?</b>\n\nСтарые штрафы сохранят свои баллы."
        rows = [
            [button(label, "type_method", version=flow, value=value)]
            for label, value in (
                ("За минуту", "PER_MINUTE"),
                ("Фиксированная сумма", "FIXED"),
                ("Вручную при назначении", "MANUAL"),
            )
        ]
    elif target == "rate":
        text = "💰 <b>Ставка штрафа</b>\n\nВведи ставку в баллах (1–1 000 000)."
        if data.get("calculation_type") == "PER_MINUTE":
            text += "\nЭто стоимость одной минуты."
        rows = [
            [
                button(str(value), "type_rate_value", version=flow, value=str(value))
                for value in (10, 50, 100)
            ]
        ]
    else:
        text = {
            "name": "⚠️ <b>Новый тип штрафа</b>\n\nНапиши название (до 80 символов):",
            "emoji": "🎨 Напиши emoji для типа штрафа:",
            "description": "💬 Добавь описание (до 500 символов) или пропусти:",
        }[target]
        rows = []
        if data.get(target):
            text += f"\n\nСейчас: {safe(data[target])}"
            rows.append([button("Оставить значение", "type_keep", version=flow, value=target)])
        if target == "description":
            rows.append([button("Пропустить", "type_skip", version=flow)])
    if target == "name":
        rows.append([button("⬅️ Типы штрафов", "types")])
    else:
        rows.append(controls("settings_back", flow))
    await render(bot, actor.telegram_id, state, text, keyboard(*rows))
