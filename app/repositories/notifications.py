from datetime import datetime

from sqlalchemy import func, select

from app.models import Notification, Task
from app.repositories.base import Repository


class NotificationRepository(Repository):
    async def next_due(self, now: datetime) -> Notification | None:
        return await self.session.scalar(
            select(Notification)
            .where(Notification.status == "pending", Notification.available_at <= now)
            .order_by(Notification.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def pending_count(self, user_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Task.id)).where(
                    Task.evaluator_user_id == user_id,
                    Task.status == "BACKLOG",
                    Task.deleted_at.is_(None),
                )
            )
            or 0
        )
