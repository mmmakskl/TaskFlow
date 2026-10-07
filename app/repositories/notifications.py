from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import Notification, Task, User
from app.repositories.base import Repository
from app.repositories.tasks import TaskRepository


class NotificationRepository(Repository):
    async def recipient(self, user_id: int) -> User:
        # NO KEY UPDATE serializes claims without blocking FK KEY SHARE locks held
        # by concurrent task inserts (which may wait on the pending-batch unique index).
        user = await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        assert user is not None  # Enforced by notifications.recipient_user_id FK.
        return user

    async def now(self) -> datetime:
        # Database clock is shared by all replicas; read AFTER obtaining recipient locks.
        now = await self.session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        return now

    async def enqueue_rating(
        self, evaluator_id: int, debounce_seconds: int, *, defer_existing: bool = False
    ) -> None:
        now = await self.now()
        statement = insert(Notification).values(
            recipient_user_id=evaluator_id,
            kind="rating_pending",
            payload={},
            dedupe_key=f"rating:{uuid4()}",
            status="pending",
            available_at=now + timedelta(seconds=debounce_seconds),
            created_at=now,
        )
        pending = (Notification.kind == "rating_pending") & (Notification.status == "pending")
        if defer_existing:
            # Explicit Telegram backoff can exceed our cooldown; never shorten it when
            # another producer already queued a pending batch during the send attempt.
            statement = statement.on_conflict_do_update(
                index_elements=[Notification.recipient_user_id],
                index_where=pending,
                set_={
                    "available_at": func.greatest(
                        Notification.available_at, statement.excluded.available_at
                    )
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=[Notification.recipient_user_id],
                index_where=pending,
            )
        await self.session.execute(statement)

    async def expire_inflight(self, now: datetime) -> None:
        await self.session.execute(
            update(Notification)
            .where(
                Notification.status == "sending",
                Notification.claimed_at < now - timedelta(minutes=5),
            )
            .values(status="uncertain", finished_at=now, error_code="interrupted")
        )

    async def next_due(self, now: datetime) -> Notification | None:
        return await self.session.scalar(
            select(Notification)
            .where(Notification.status == "pending", Notification.available_at <= now)
            .order_by(Notification.available_at, Notification.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def pending_count(self, user_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Task.id)).where(
                    TaskRepository.visible(),
                    Task.evaluator_user_id == user_id,
                    Task.status == "BACKLOG",
                    Task.deleted_at.is_(None),
                )
            )
            or 0
        )
