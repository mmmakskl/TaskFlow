import pytest
from pydantic import ValidationError

from app.bot.callbacks.actions import Action, callback


@pytest.mark.parametrize(
    "wire",
    [
        "a:menu:0:0:",
        "a:list:100000:0:active",
        "a:status_confirm:2147483647:2147483647:IN_PROGRESS",
        "a:deadline:0:2147483647:2100-12-31",
        "a:penalty_type:2147483647:2147483647:2147483647",
        "a:penalty_commit:0:2147483647:0123456789abcdef0123456789abcdef",
        "a:penalty_delete_confirm:2147483647:2147483647:",
    ],
)
def test_existing_callback_wire_format_remains_stable(wire: str) -> None:
    assert Action.unpack(wire).pack() == wire


def test_penalty_confirmation_fits_with_two_bytes_to_spare() -> None:
    wire = callback("penalty_commit", version=2147483647, value="f" * 32)
    assert len(wire.encode("utf-8")) == 62
    assert Action.unpack(wire).value == "f" * 32


def test_callback_accepts_exactly_64_bytes() -> None:
    wire = callback("penalty_commit", entity=100, version=2147483647, value="f" * 32)
    assert len(wire.encode("utf-8")) == 64
    assert Action.unpack(wire).pack() == wire


@pytest.mark.parametrize(
    "wire",
    [
        "a:status:-1:1:DONE",
        "a:status:1:-1:DONE",
        "a:status:2147483648:1:DONE",
        "a:status:1:2147483648:DONE",
        "a:menu:0:0:" + "x" * 33,
        "a:MENU:0:0:",
        "a:menu:0:0:📝",
        "a:menu:0:0:with space",
        "a:penalty_commit:0:2147483647:" + "f" * 33,
        "a:penalty_commit:1000:2147483647:" + "f" * 32,
    ],
)
def test_invalid_callback_is_rejected_before_dispatch(wire: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        Action.unpack(wire)


def test_unknown_action_can_reach_stale_router() -> None:
    assert Action.unpack("a:future_action:0:0:").name == "future_action"
