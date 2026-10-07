"""Reject JSON values PostgreSQL cannot store before opening an inbox transaction."""

import math
from typing import Any

from app.utils.text import postgres_text


def validate_payload(value: Any) -> None:
    if isinstance(value, str) and not postgres_text(value):
        raise ValueError("Invalid Unicode")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    if isinstance(value, dict):
        for key, item in value.items():
            validate_payload(key)
            validate_payload(item)
    elif isinstance(value, list):
        for item in value:
            validate_payload(item)
