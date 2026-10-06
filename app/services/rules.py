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
    if kind == "PER_MINUTE":
        points = positive_integer(quantity) * positive_integer(rate, 1000000)
    elif kind == "FIXED":
        points = positive_integer(rate, 1000000)
    elif kind == "MANUAL":
        points = positive_integer(manual, 1000000000)
    else:
        raise DomainError("Неизвестный способ расчёта штрафа.")
    return positive_integer(points, 1000000000)


def transition(old: str, new: str, confirmed: bool = False) -> None:
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
