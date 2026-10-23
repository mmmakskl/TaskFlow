"""Bounded, escaped notification templates; no database or delivery side effects."""

from aiogram.types import InlineKeyboardMarkup

from app.bot.render import safe
from app.models import Notification


def notification_message(
    row: Notification, pending_count: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    payload = row.payload
    if row.kind == "rating_pending":
        return (
            f"⭐ <b>Есть новые задачи на оценку</b>\n\nСейчас ждут твоей оценки: {pending_count}",
            None,
        )
    if row.kind == "task_rejected":
        return (
            f"🚫 {safe(payload['actor'])}: предложение отклонено\n\n📝 {safe(payload['title'])}",
            None,
        )
    if row.kind == "rated":
        amount = (
            f"{payload['old_points']} → {payload['points']}"
            if "old_points" in payload
            else str(payload["points"])
        )
        if payload.get("shared"):
            amount += " каждому"
        status = (
            "▶️ Задача остаётся в работе."
            if payload.get("status") == "IN_PROGRESS"
            else "Задача добавлена в «К выполнению»."
        )
        return (
            f"⭐ {safe(payload['evaluator'])}: задача оценена\n\n"
            f"📝 {safe(payload['title'])}\n💰 {amount} баллов\n\n" + status,
            None,
        )
    if row.kind == "penalty_created":
        return (
            f"⚠️ Тебе назначен штраф\n\n👤 {safe(payload['actor'])}\n"
            f"📝 {safe(payload['name'])}\n💸 −{payload['points']} баллов",
            None,
        )
    if row.kind == "handler_error":
        return (
            "⚠️ Не получилось выполнить действие. Попробуй ещё раз или открой меню.",
            None,
        )
    raise ValueError("unsupported_notification")
