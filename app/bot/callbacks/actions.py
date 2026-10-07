from typing import Self

from aiogram.filters.callback_data import CallbackData
from pydantic import Field, model_validator


class Action(CallbackData, prefix="a"):
    name: str = Field(min_length=1, max_length=22, pattern=r"^[a-z][a-z_]*$")
    entity: int = Field(default=0, ge=0, le=2147483647)
    version: int = Field(default=0, ge=0, le=2147483647)
    value: str = Field(default="", max_length=32, pattern=r"^[A-Za-z0-9_-]*$")

    @model_validator(mode="after")
    def validate_payload_size(self) -> Self:
        # aiogram pack checks Telegram's UTF-8 byte limit for inbound models too.
        self.pack()
        return self


def callback(name: str, entity: int = 0, version: int = 0, value: str = "") -> str:
    return Action(name=name, entity=entity, version=version, value=value).pack()
