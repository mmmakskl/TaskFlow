from app.bot.keyboards.common import Button, button
from app.utils.duration import duration


def controls(name: str, flow: int, value: str = "") -> list[Button]:
    return [button("⬅️ Назад", name, version=flow, value=value), button("❌ Отмена", "menu")]


def minute_choices(name: str, flow: int, values: tuple[int, ...]) -> list[list[Button]]:
    buttons = [button(duration(value), name, version=flow, value=str(value)) for value in values]
    return [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
