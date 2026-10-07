from datetime import timedelta

import pytest

from app.models import AuditEvent, Notification, ScoreEvent, Task, User
from app.services.rules import DomainError, StaleAction
from tests.business_fakes import NOW


async def rated(business, points=500):
    task = await business.tasks.create(business.max, " Task ", "create")
    await business.tasks.rate(business.vika, task.id, points, task.version)
    return task


async def test_creation_uses_authenticated_owner_peer_backlog_and_one_batch(business):
    first = await business.tasks.create(business.max, " Task ", "create")
    assert first.owner_user_id == business.max.id and first.evaluator_user_id == business.vika.id
    assert first.title == "Task" and first.status == "BACKLOG"
    assert (
        first.rated_points is None and first.awarded_points is None and first.completed_at is None
    )
    second = await business.tasks.create(business.max, "Second", "create-2")
    assert second.id != first.id
    assert len(business.session.of(Notification)) == 1
    assert len(business.session.of(AuditEvent)) == 2
    assert await business.tasks.view(business.vika, first.id) is first
    assert (await business.results.week(at=NOW))[1][0][1:] == (0, 0)


async def test_duplicate_creation_no_audit_or_notification_side_effect(business):
    first = await business.tasks.create(business.max, "Task", "create")
    assert await business.tasks.create(business.max, "Task", "create") is first
    assert len(business.session.of(Task)) == len(business.session.of(AuditEvent)) == 1
    with pytest.raises(DomainError):
        await business.tasks.create(business.vika, "Task", "create")


@pytest.mark.parametrize("actor", ["unknown", "forged"])
async def test_actor_identity_cannot_be_forged(business, actor):
    user = User(
        id=3 if actor == "forged" else 99,
        telegram_id=business.max.telegram_id if actor == "forged" else 9999,
    )
    with pytest.raises(DomainError):
        await business.tasks.create(user, "Task", "forged")
    assert not business.session.of(Task)


async def test_missing_peer_rejects_creation(business):
    business.session.rows.remove(business.vika)
    with pytest.raises(DomainError):
        await business.tasks.create(business.max, "Task", "create")
    assert not business.session.of(Task)


@pytest.mark.parametrize("title", ["", " ", "x" * 201])
async def test_title_validation(business, title):
    with pytest.raises(DomainError):
        await business.tasks.create(business.max, title, "create")
    assert not business.session.of(Task)


@pytest.mark.parametrize(
    "changes", [{"comment": "x" * 1501}, {"deadline": NOW.replace(tzinfo=None)}, {"estimate": 0}]
)
async def test_creation_validates_optional_fields(business, changes):
    with pytest.raises(DomainError):
        await business.tasks.create(business.max, "Task", "create", **changes)
    assert not business.session.of(Task)


async def test_rating_permissions_state_and_duplicate(business):
    task = await business.tasks.create(business.max, "Task", "create")
    with pytest.raises(DomainError):
        await business.tasks.rate(business.max, task.id, 500, task.version)
    version = task.version
    await business.tasks.rate(business.vika, task.id, 500, version)
    assert task.status == "TODO" and task.rated_points == 500
    assert task.rated_by_user_id == business.vika.id and task.rated_at == NOW
    assert task.awarded_points is None
    assert not business.session.of(ScoreEvent)
    with pytest.raises(StaleAction):
        await business.tasks.rate(business.vika, task.id, 500, version)
    with pytest.raises(DomainError):
        await business.tasks.rate(business.vika, task.id, 500, task.version)
    assert len([event for event in business.session.of(AuditEvent) if event.action == "rate"]) == 1
    assert len([event for event in business.session.of(Notification) if event.kind == "rated"]) == 1


@pytest.mark.parametrize("status", ["TODO", "IN_PROGRESS"])
async def test_completion_award_snapshot_and_no_duplicate(business, status):
    task = await rated(business)
    if status == "IN_PROGRESS":
        await business.tasks.change_status(business.max, task.id, status, task.version)
        assert task.started_at == NOW
    task.created_at = NOW - timedelta(days=30)
    version = task.version
    await business.tasks.change_status(business.max, task.id, "DONE", version)
    assert task.awarded_points == 500 and task.completed_at == NOW
    task.rated_points = 999
    assert (await business.results.week(at=NOW))[1][0][1] == 500
    with pytest.raises(StaleAction):
        await business.tasks.change_status(business.max, task.id, "DONE", version)
    assert len(business.session.of(ScoreEvent)) == 1
    assert business.session.of(ScoreEvent)[0].effective_at == NOW


@pytest.mark.parametrize("status", ["BACKLOG", "TODO", "IN_PROGRESS", "DONE"])
async def test_cancellation_removes_all_points(business, status):
    task = await business.tasks.create(business.max, "Task", "create")
    if status != "BACKLOG":
        await business.tasks.rate(business.vika, task.id, 500, task.version)
    if status in ("IN_PROGRESS", "DONE"):
        await business.tasks.change_status(business.max, task.id, status, task.version)
    await business.tasks.change_status(business.max, task.id, "CANCELLED", task.version, True)
    assert task.cancelled_at == NOW and task.completed_at is None and task.awarded_points is None
    assert (await business.results.week(at=NOW))[1][0][1] == 0


@pytest.mark.parametrize("new_status", ["TODO", "IN_PROGRESS"])
async def test_reopen_reverses_original_week_and_recompletion_uses_new_week(
    business, monkeypatch, new_status
):
    task = await rated(business)
    original = NOW - timedelta(days=7)
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: original)
    await business.tasks.change_status(business.max, task.id, "DONE", task.version)
    with pytest.raises(DomainError):
        await business.tasks.change_status(business.max, task.id, new_status, task.version)
    version = task.version
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: NOW)
    await business.tasks.change_status(business.max, task.id, new_status, version, True)
    assert task.awarded_points is None and task.completed_at is None
    assert (await business.results.week(at=NOW, offset=-1))[1][0][1] == 0
    with pytest.raises(StaleAction):
        await business.tasks.change_status(business.max, task.id, new_status, version, True)
    await business.tasks.change_status(business.max, task.id, "DONE", task.version)
    assert (await business.results.week(at=NOW))[1][0][1] == 500
    assert (await business.results.week(at=NOW, offset=-1))[1][0][1] == 0
    assert sum(event.points for event in business.session.of(ScoreEvent)) == 500
    event = next(
        event for event in business.session.of(AuditEvent) if event.old_data.get("status") == "DONE"
    )
    assert event.old_data["awarded_points"] == 500 and event.new_data["awarded_points"] is None


@pytest.mark.parametrize("action", ["rate", "edit", "status", "view"])
async def test_soft_deleted_task_is_inaccessible(business, action):
    task = await business.tasks.create(business.max, "Task", "create")
    task.deleted_at = NOW
    with pytest.raises(DomainError):
        if action == "rate":
            await business.tasks.rate(business.vika, task.id, 500, task.version)
        elif action == "edit":
            await business.tasks.edit(business.max, task.id, task.version, title="Changed")
        elif action == "status":
            await business.tasks.change_status(
                business.max, task.id, "CANCELLED", task.version, True
            )
        else:
            await business.tasks.view(business.max, task.id)


async def test_owner_only_status_and_edit(business):
    task = await rated(business)
    with pytest.raises(DomainError):
        await business.tasks.change_status(business.vika, task.id, "DONE", task.version)
    with pytest.raises(DomainError):
        await business.tasks.edit(business.vika, task.id, task.version, title="Changed")
    await business.tasks.edit(
        business.max,
        task.id,
        task.version,
        title=" Changed ",
        comment=" Note ",
        deadline=NOW,
        estimated_minutes=90,
    )
    assert task.title == "Changed" and task.comment == "Note" and task.estimated_minutes == 90
    await business.tasks.edit(
        business.max, task.id, task.version, comment=None, deadline=None, estimated_minutes=None
    )
    assert task.comment is None and task.deadline is None and task.estimated_minutes is None
    assert task.status == "BACKLOG" and task.rated_points is None
    await business.tasks.rate(business.vika, task.id, 500, task.version)
    await business.tasks.change_status(business.max, task.id, "DONE", task.version)
    with pytest.raises(DomainError):
        await business.tasks.edit(business.max, task.id, task.version, title="Changed")


@pytest.mark.parametrize(
    "change",
    [
        {"bogus": 1},
        {"title": True},
        {"deadline": NOW.replace(tzinfo=None)},
        {"comment": "x" * 1501},
        {"estimated_minutes": -1},
    ],
)
async def test_invalid_edit_is_atomic_even_when_error_is_caught(business, change):
    task = await rated(business)
    before = task.version
    changes = {"title": "Partial", **change}
    with pytest.raises(DomainError):
        await business.tasks.edit(business.max, task.id, task.version, **changes)
    assert task.title == "Task" and task.version == before
    assert len([event for event in business.session.of(AuditEvent) if event.action == "edit"]) == 0


@pytest.mark.parametrize("action", ["create", "rate", "done", "edit"])
async def test_failed_audit_has_no_partial_task_side_effect(business, monkeypatch, action):
    b = business
    task = (
        await rated(b)
        if action in ("done", "edit")
        else await b.tasks.create(b.max, "Task", "first")
        if action == "rate"
        else None
    )
    audit_count = len(b.session.of(AuditEvent))
    notification_count = len(b.session.of(Notification))

    def fail(*args):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(b.tasks.repo, "audit", fail)
    with pytest.raises(RuntimeError):
        if action == "create":
            await b.tasks.create(b.max, "Task", "create")
        elif action == "rate":
            await b.tasks.rate(b.vika, task.id, 500, task.version)
        elif action == "done":
            await b.tasks.change_status(b.max, task.id, "DONE", task.version)
        else:
            await b.tasks.edit(b.max, task.id, task.version, title="Changed")
    assert len(b.session.of(AuditEvent)) == audit_count
    assert len(b.session.of(Notification)) == notification_count
    assert not b.session.of(ScoreEvent)
    if task:
        assert task.status == ("BACKLOG" if action == "rate" else "TODO") and task.title == "Task"
    else:
        assert not b.session.of(Task)


async def test_corrupted_unrated_active_task_cannot_complete(business):
    b = business
    task = await b.tasks.create(b.max, "Task", "task")
    task.status = "TODO"  # Simulate corruption; PostgreSQL itself rejects this shape.
    with pytest.raises(DomainError):
        await b.tasks.change_status(b.max, task.id, "DONE", task.version)
    assert not b.session.of(ScoreEvent)


async def test_empty_edit_rejected(business):
    task = await rated(business)
    with pytest.raises(DomainError):
        await business.tasks.edit(business.max, task.id, task.version)
