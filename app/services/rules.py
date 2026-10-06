class DomainError(Exception):
    """Safe, user-facing domain validation failure."""


class StaleAction(DomainError):
    def __init__(self) -> None:
        super().__init__("Эта кнопка устарела. Обнови список задач.")


def rating_points(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 10000:
        raise DomainError("Введи целое число от 1 до 10 000.")
    return value


def positive_integer(value: object, maximum: int = 10000) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise DomainError(f"Введи целое число от 1 до {maximum:,}.")
    return value


def penalty_points(kind: str, rate: int, quantity: int | None, manual: int | None) -> int:
    if type(rate) is not int or not 0 <= rate <= 1000000:
        raise DomainError("Недопустимая ставка штрафа.")
    if kind == "PER_MINUTE":
        if manual is not None:
            raise DomainError("Для этого штрафа укажи только минуты.")
        points = positive_integer(quantity) * positive_integer(rate, 1000000)
    elif kind == "FIXED":
        if quantity is not None or manual is not None:
            raise DomainError("Фиксированный штраф не требует количества или суммы.")
        points = positive_integer(rate, 1000000)
    elif kind == "MANUAL":
        if quantity is not None:
            raise DomainError("Для ручного штрафа укажи только баллы.")
        points = positive_integer(manual, 1000000000)
    else:
        raise DomainError("Неизвестный способ расчёта штрафа.")
    return positive_integer(points, 1000000000)


def transition(old: str, new: str, confirmed: bool = False) -> None:
    if type(confirmed) is not bool:
        raise DomainError("Недопустимое подтверждение.")
    allowed = {
        "BACKLOG": {"CANCELLED"},
        "TODO": {"IN_PROGRESS", "DONE", "CANCELLED"},
        "IN_PROGRESS": {"TODO", "DONE", "CANCELLED"},
        "DONE": {"TODO", "IN_PROGRESS", "CANCELLED"},
        "CANCELLED": set(),
    }
    if new not in allowed.get(old, set()):
        raise DomainError("Это действие больше недоступно. Обнови задачу.")
    if (old == "DONE" or new == "CANCELLED") and not confirmed:
        raise DomainError("Подтверди изменение: оно влияет на результаты.")


def text_field(value: str, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise DomainError(f"{label}: введи текст.")
    value = value.strip()
    if not 1 <= len(value) <= maximum:
        raise DomainError(f"{label}: от 1 до {maximum} символов.")
    return value


def estimated_minutes(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 10080:
        raise DomainError("Введи длительность в минутах: целое число от 1 до 10 080.")
    return value


def authenticated_identity(
    actor_id: int,
    telegram_id: int,
    stored_id: int,
    stored_telegram_id: int,
    allowed: set[int] | frozenset[int],
) -> None:
    if (
        type(actor_id) is not int
        or type(telegram_id) is not int
        or actor_id != stored_id
        or telegram_id != stored_telegram_id
        or telegram_id not in allowed
    ):
        raise DomainError("🔒 Доступ запрещён.")


def operation_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise DomainError("Недопустимый идентификатор операции.")
    return value
