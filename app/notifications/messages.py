"""Bounded, escaped notification templates; no database or delivery side effects."""

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.common import button, keyboard
from app.bot.render import safe
from app.models import Notification


def notification_message(row: Notification, pending_count: int) -> tuple[str, InlineKeyboardMarkup]:
    payload = row.payload
    if row.kind == "rating_pending":
        return (
            f"⭐ <b>Есть новые задачи на оценку</b>\n\nСейчас ждут твоей оценки: {pending_count}",
            keyboard([button("Оценить задачи", "ratings")]),
        )
    if row.kind == "rated":
        return (
            f"⭐ {safe(payload['evaluator'])}: задача оценена\n\n"
            f"📝 {safe(payload['title'])}\n💰 {payload['points']} баллов\n\n"
            "Задача добавлена в «К выполнению».",
            keyboard([button("Открыть задачу", "task", payload["task_id"])]),
        )
    if row.kind == "penalty_created":
        return (
            f"⚠️ Тебе назначен штраф\n\n👤 {safe(payload['actor'])}\n"
            f"📝 {safe(payload['name'])}\n💸 −{payload['points']} баллов",
            keyboard([button("⚠️ К штрафам", "penalty_list")]),
        )
    if row.kind == "handler_error":
        return (
            "⚠️ Не получилось выполнить действие. Попробуй ещё раз или открой меню.",
            keyboard([button("🏠 Меню", "menu")]),
        )
    raise ValueError("unsupported_notification")
