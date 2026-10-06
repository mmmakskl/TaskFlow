from datetime import timedelta

import pytest

from app.models import Notification, Task
from app.repositories.notifications import NotificationRepository
from app.services.notifications import NotificationService
from tests.business_fakes import NOW, MemoryRepository


class MemoryNotifications(MemoryRepository, NotificationRepository):
    async def next_due(self, now):
        return next(
            (
                row
                for row in self.session.of(Notification)
                if row.status == "pending" and row.available_at <= now
            ),
            None,
        )

    async def pending_count(self, user_id):
        return sum(
            row.evaluator_user_id == user_id and row.status == "BACKLOG" and not row.deleted_at
            for row in self.session.of(Task)
        )


@pytest.mark.parametrize(
    "minutes,allowed", [(None, True), (59, False), (60, True), (61, True), (-1, False)]
)
async def test_persistent_cooldown_boundary(business, monkeypatch, minutes, allowed):
    b = business
    monkeypatch.setattr("app.services.notifications.utcnow", lambda: NOW)
    service = NotificationService(MemoryNotifications(b.session), b.settings)
    for i in range(10):
        await b.tasks.create(b.max, f"Task {i}", f"task-{i}")
    b.vika.last_rating_notification_at = (
        NOW - timedelta(minutes=minutes) if minutes is not None else None
    )
    claimed = await service.claim()
    assert (claimed is not None) == allowed
    if allowed:
        assert claimed[2] == 10 and b.vika.last_rating_notification_at == NOW
    assert len(b.session.of(Notification)) == 1
    assert await service.claim() is None


async def test_empty_pending_batch_is_skipped(business, monkeypatch):
    b = business
    monkeypatch.setattr("app.services.notifications.utcnow", lambda: NOW)
    task = await b.tasks.create(b.max, "Task", "task")
    await b.tasks.rate(b.vika, task.id, 500, task.version)
    service = NotificationService(MemoryNotifications(b.session), b.settings)
    assert await service.claim() is None
    assert b.vika.last_rating_notification_at is None
    # Rating-completed notification does not consume rating-pending cooldown.
    assert (await service.claim())[1].id == b.max.id
    assert await service.claim() is None
