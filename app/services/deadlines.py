"""Deadline eligibility shared by task presentation and manual penalty entry."""

from datetime import UTC, datetime, timedelta

from app.models import Task


def deadline_delay(task: Task, at: datetime) -> timedelta | None:
    if task.deadline is None or task.status == "CANCELLED":
        return None
    end = task.completed_at if task.status == "DONE" else at
    if end is None:
        return None
    delay = end.astimezone(UTC) - task.deadline.astimezone(UTC)
    return delay if delay > timedelta(0) else None


def delay_label(delay: timedelta) -> str:
    minutes = int(delay.total_seconds() // 60)
    if minutes < 1:
        return "меньше минуты"
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    return " ".join(parts)
