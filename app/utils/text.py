"""Text accepted by PostgreSQL UTF-8 text and JSONB columns."""


def postgres_text(value: str) -> bool:
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
