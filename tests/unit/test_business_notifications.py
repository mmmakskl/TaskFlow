from datetime import timedelta

import pytest

from app.models import Notification, Task, User
from app.repositories.notifications import NotificationRepository
from app.services.notifications import NotificationService
from tests.business_fakes import NOW, MemoryRepository


class MemoryNotifications(MemoryRepository, NotificationRepository):
    async def recipient(self, user_id):
        return await self.get(User, user_id, lock=True)

    async def now(self):
        return NOW

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
        assert claimed[0].attempts == 1 and claimed[0].claimed_at == NOW
    else:
        row = b.session.of(Notification)[0]
        assert row.status == "pending" and row.attempts == 0
        assert row.available_at == b.vika.last_rating_notification_at + timedelta(minutes=60)
    assert len(b.session.of(Notification)) == 1
    assert await service.claim() is None


async def test_penalty_outbox_snapshot_is_atomic_and_idempotent(business):
    b = business
    draft = await b.penalties.preview(b.kind.id, 5, None)
    await b.penalties.create(b.vika, b.max.id, draft, "notify-penalty", True)
    await b.penalties.create(b.vika, b.max.id, draft, "notify-penalty", True)
    rows = b.session.of(Notification)
    assert len(rows) == 1 and rows[0].recipient_user_id == b.max.id
    assert rows[0].payload["points"] == 500
    await b.penalties.configure(b.vika, b.kind.id, b.kind.version, rate=150)
    assert rows[0].payload["points"] == 500


@pytest.mark.parametrize("kind", ["rated", "penalty_created"])
def test_notification_templates_escape_user_text_and_have_navigation(kind):
    from app.notifications.messages import notification_message

    row = Notification(
        kind=kind,
        payload={
            "evaluator": "<b>Вика</b>",
            "title": "<script> & task",
            "task_id": 1,
            "points": 500,
            "actor": "<b>Вика</b>",
            "name": "<script> & penalty",
        },
    )
    text, markup = notification_message(row, 0)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "&lt;b&gt;Вика&lt;/b&gt;" in text
    assert markup.inline_keyboard[0][0].callback_data


async def test_empty_pending_batch_is_skipped(business, monkeypatch):
    b = business
    task = await b.tasks.create(b.max, "Task", "task")
    await b.tasks.rate(b.vika, task.id, 500, task.version)
    service = NotificationService(MemoryNotifications(b.session), b.settings)
    assert await service.claim() is None

    assert b.vika.last_rating_notification_at is None
    # Rating-completed notification does not consume rating-pending cooldown.
    assert (await service.claim())[1].id == b.max.id
    assert await service.claim() is None


async def test_cooldown_clock_is_read_after_recipient_lock(business):
    from unittest.mock import AsyncMock

    b = business
    await b.tasks.create(b.max, "Task", "clock")
    b.vika.last_rating_notification_at = NOW
    repo = MemoryNotifications(b.session)
    later = NOW + timedelta(minutes=61)
    repo.now = AsyncMock(side_effect=[NOW, later])
    claimed = await NotificationService(repo, b.settings).claim()
    assert claimed is not None and b.vika.last_rating_notification_at == later


async def test_cooldown_is_per_recipient_not_global(business):
    b = business
    await b.tasks.create(b.max, "For Vika", "to-vika")
    await b.tasks.create(b.vika, "For Max", "to-max")
    service = NotificationService(MemoryNotifications(b.session), b.settings)
    first, second = await service.claim(), await service.claim()
    assert {first[1].id, second[1].id} == {b.max.id, b.vika.id}
    assert b.max.last_rating_notification_at == b.vika.last_rating_notification_at == NOW
