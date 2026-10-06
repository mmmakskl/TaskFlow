import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks.actions import callback

Button = tuple[str, str]


def keyboard(*rows: list[Button]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def button(label: str, name: str, entity: int = 0, version: int = 0, value: str = "") -> Button:
    return label, callback(name, entity, version, value)


def cancel() -> InlineKeyboardMarkup:
    return keyboard([button("❌ Отмена", "menu")])


def pager(name: str, page: int, has_next: bool, value: str = "") -> list[Button]:
    # Adapted from vintikzq/FastAPI-todo-Telegram-Bot src/keyboards/task_menu.py (MIT).
    buttons: list[Button] = []
    if page > 0:
        buttons.append(button("◀️", name, page - 1, value=value))
    buttons.append(button(f"• {page + 1} •", "noop"))
    if has_next:
        buttons.append(button("▶️", name, page + 1, value=value))
    return buttons


def inline_calendar(year: int, month: int, flow: int = 0) -> InlineKeyboardMarkup:
    if not 2020 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("Invalid calendar month")
    previous = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    rows = [
        [
            button("◀️", "calendar", version=flow, value=previous.isoformat()),
            button(f"{month:02d}.{year}", "noop"),
            button("▶️", "calendar", version=flow, value=following.isoformat()),
        ]
    ]
    rows.append([button(day, "noop") for day in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")])
    for week in calendar.monthcalendar(year, month):
        rows.append(
            [
                button(str(day), "deadline", version=flow, value=date(year, month, day).isoformat())
                if day
                else button(" ", "noop")
                for day in week
            ]
        )
    rows.append([button("Без дедлайна", "deadline", version=flow, value="none")])
    rows.append([button("❌ Отмена", "menu")])
    return keyboard(*rows)
