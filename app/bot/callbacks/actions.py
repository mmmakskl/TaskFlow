from aiogram.filters.callback_data import CallbackData


class Action(CallbackData, prefix="a"):
    name: str
    entity: int = 0
    version: int = 0
    value: str = ""


def callback(name: str, entity: int = 0, version: int = 0, value: str = "") -> str:
    return Action(name=name, entity=entity, version=version, value=value).pack()
