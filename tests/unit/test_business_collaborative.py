from datetime import timedelta

import pytest

from app.models import AuditEvent, Notification, ScoreEvent, TaskTarget
from app.services.rules import DomainError, StaleAction
from app.services.templates import TaskTemplateService
from tests.business_fakes import NOW, MemoryTemplates


async def template_service(business):
    return TaskTemplateService(MemoryTemplates(business.session), business.settings)


async def test_shared_completion_and_original_week_reopen_are_dual_atomic(business, monkeypatch):
    b = business
    task = await b.tasks.create(
        b.max, "Together", "shared", target=TaskTarget.SHARED, proposed_points=500
    )
    assert task.participant_user_ids == (b.max.id, b.vika.id)
    await b.tasks.rate(b.vika, task.id, 700, task.version)
    first = task.first_accepted_at
    original = NOW - timedelta(days=7)
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: original)
    await b.tasks.change_status(b.vika, task.id, "DONE", task.version)
    assert [(e.user_id, e.points) for e in b.session.of(ScoreEvent)] == [(1, 700), (2, 700)]
    version = task.version
    with pytest.raises(StaleAction):
        await b.tasks.change_status(b.max, task.id, "DONE", version - 1)
    monkeypatch.setattr("app.services.tasks.utcnow", lambda: NOW)
    await b.tasks.change_status(b.max, task.id, "TODO", version, True)
    assert len(b.session.of(ScoreEvent)) == 4
    assert all(e.effective_at == original for e in b.session.of(ScoreEvent))
    assert task.first_accepted_at == first
    assert sum(e.points for e in b.session.of(ScoreEvent)) == 0


async def test_peer_creation_idempotency_and_edit_rights_survive_reapproval(business):
    b = business
    task = await b.tasks.create(b.max, "For peer", "peer", target=TaskTarget.PEER)
    assert task.owner_user_id == b.vika.id and task.creator_user_id == b.max.id
    assert await b.tasks.create(b.max, "Retry", "peer") is task
    with pytest.raises(DomainError):
        await b.tasks.create(b.vika, "Collision", "peer")
    with pytest.raises(DomainError):
        await b.tasks.edit(b.vika, task.id, task.version, title="Premature")
    await b.tasks.edit(b.max, task.id, task.version, title="Updated")
    await b.tasks.rate(b.vika, task.id, 300, task.version)
    first = task.first_accepted_at
    with pytest.raises(DomainError):
        await b.tasks.edit(b.max, task.id, task.version, title="Forbidden")
    await b.tasks.edit(b.vika, task.id, task.version, title="Reapproval")
    assert task.status == "BACKLOG" and task.proposed_points == 300
    assert task.evaluator_user_id == b.max.id and task.first_accepted_at == first
    with pytest.raises(DomainError):
        await b.tasks.change_status(b.vika, task.id, "DONE", task.version)
    with pytest.raises(DomainError):
        await b.tasks.edit(b.max, task.id, task.version, title="Still forbidden")
    await b.tasks.rate(b.max, task.id, 350, task.version)
    await b.tasks.change_status(b.vika, task.id, "DONE", task.version)
    assert b.session.of(ScoreEvent)[0].user_id == b.vika.id


async def test_rerating_author_confirmation_noop_and_status_preservation(business):
    b = business
    task = await b.tasks.create(b.max, "Work", "work")
    await b.tasks.rate(b.vika, task.id, 200, task.version)
    await b.tasks.change_status(b.max, task.id, "IN_PROGRESS", task.version)
    version = task.version
    audits = len(b.session.of(AuditEvent))
    await b.tasks.rerate(b.vika, task.id, 200, version)
    assert task.version == version and len(b.session.of(AuditEvent)) == audits
    with pytest.raises(DomainError):
        await b.tasks.rerate(b.max, task.id, 300, version, True)
    with pytest.raises(DomainError):
        await b.tasks.rerate(b.vika, task.id, 300, version)
    await b.tasks.rerate(b.vika, task.id, 300, version, True)
    assert task.status == "IN_PROGRESS" and task.rated_points == 300 and task.started_at == NOW
    notice = [n for n in b.session.of(Notification) if n.kind == "rated"][-1]
    assert notice.payload["old_points"] == 200 and notice.payload["status"] == "IN_PROGRESS"
    assert notice.recipient_user_id == b.max.id
    with pytest.raises(StaleAction):
        await b.tasks.rerate(b.vika, task.id, 400, version, True)
    await b.tasks.change_status(b.max, task.id, "DONE", task.version)
    with pytest.raises(DomainError):
        await b.tasks.rerate(b.vika, task.id, 500, task.version, True)


async def test_templates_snapshot_stale_disable_and_semantic_draft_edits(business):
    b = business
    service = await template_service(b)
    template = await service.create(b.max, "English", 500, "template", 60)
    assert await service.create(b.max, "Retry", 1, "template") is template
    with pytest.raises(DomainError):
        await service.create(b.vika, "Collision", 1, "template")
    task = await b.tasks.create(
        b.max,
        "English",
        "auto",
        estimate=60,
        proposed_points=500,
        template_id=template.id,
        template_version=template.version,
    )
    assert (
        task.status == "TODO" and task.rating_source == "TEMPLATE" and task.rated_by_user_id is None
    )
    with pytest.raises(DomainError):
        await b.tasks.rerate(b.vika, task.id, 600, task.version, True)
    await b.tasks.edit(b.max, task.id, task.version, comment="Note", deadline=NOW)
    assert task.status == "TODO"
    await service.edit(b.vika, template.id, template.version, points=700)
    assert task.rated_points == 500 and task.template_snapshot["points"] == 500
    with pytest.raises(StaleAction):
        await b.tasks.create(
            b.max,
            "English",
            "stale",
            estimate=60,
            proposed_points=500,
            template_id=template.id,
            template_version=1,
        )
    modified = await b.tasks.create(
        b.max,
        "Changed",
        "manual",
        estimate=60,
        proposed_points=700,
        template_id=template.id,
        template_version=2,
    )
    assert modified.status == "BACKLOG"
    peer = await b.tasks.create(
        b.max,
        "English",
        "peer-auto",
        target=TaskTarget.PEER,
        estimate=60,
        proposed_points=700,
        template_id=template.id,
        template_version=2,
    )
    assert peer.status == "BACKLOG"
    await service.disable(b.max, template.id, template.version)
    with pytest.raises(StaleAction):
        await b.tasks.create(
            b.max,
            "English",
            "disabled",
            estimate=60,
            proposed_points=700,
            template_id=template.id,
            template_version=template.version,
        )
    assert await service.list(b.max) == []
    await b.tasks.edit(b.max, task.id, task.version, estimated_minutes=90)
    assert task.status == "BACKLOG" and task.rated_points is None and task.rating_source is None


async def test_shared_failed_second_award_rolls_back_both(business, monkeypatch):
    b = business
    task = await b.tasks.create(b.max, "Shared", "shared", target=TaskTarget.SHARED)
    await b.tasks.rate(b.vika, task.id, 400, task.version)
    add = b.tasks.repo.add

    def fail_second(entity):
        if isinstance(entity, ScoreEvent) and entity.user_id == b.vika.id:
            raise RuntimeError("second award failed")
        add(entity)

    monkeypatch.setattr(b.tasks.repo, "add", fail_second)
    with pytest.raises(RuntimeError):
        await b.tasks.change_status(b.vika, task.id, "DONE", task.version)
    assert task.status == "TODO" and task.awarded_points is None
    assert b.session.of(ScoreEvent) == []


async def test_rejection_needs_confirmation_and_rejector_permissions(business):
    b = business
    task = await b.tasks.create(b.max, "Offer", "offer", target=TaskTarget.PEER)
    with pytest.raises(DomainError):
        await b.tasks.reject(b.max, task.id, task.version, True)
    with pytest.raises(DomainError):
        await b.tasks.reject(b.vika, task.id, task.version)
    await b.tasks.reject(b.vika, task.id, task.version, True)
    assert task.status == "CANCELLED" and not b.session.of(ScoreEvent)
    notice = [n for n in b.session.of(Notification) if n.kind == "task_rejected"][0]
    assert notice.recipient_user_id == b.max.id and notice.payload["status"] == "CANCELLED"
    with pytest.raises(StaleAction):
        await b.tasks.reject(b.vika, task.id, task.version - 1, True)
    assert len([n for n in b.session.of(Notification) if n.kind == "task_rejected"]) == 1


@pytest.mark.parametrize(
    "field",
    [
        "creator_user_id",
        "owner_user_id",
        "shared_user_id",
        "proposal_by_user_id",
        "evaluator_user_id",
        "rated_by_user_id",
    ],
)
@pytest.mark.parametrize(
    "action", ["view", "rate", "reject", "change_status", "edit", "rerate", "retry"]
)
async def test_every_direct_task_boundary_rejects_rogue_participant(business, field, action):
    b = business
    task = await b.tasks.create(b.max, "Original", "original", target=TaskTarget.SHARED)
    await b.tasks.rate(b.vika, task.id, 500, task.version)
    setattr(task, field, 999)
    before = b.tasks.edit_snapshot(task)
    counts = [len(b.session.of(model)) for model in (AuditEvent, Notification, ScoreEvent)]
    with pytest.raises(DomainError):
        if action == "view":
            await b.tasks.view(b.max, task.id)
        elif action == "rate":
            await b.tasks.rate(b.vika, task.id, 600, task.version)
        elif action == "reject":
            await b.tasks.reject(b.vika, task.id, task.version, True)
        elif action == "change_status":
            await b.tasks.change_status(b.max, task.id, "DONE", task.version)
        elif action == "edit":
            await b.tasks.edit(b.max, task.id, task.version, title="Changed")
        elif action == "rerate":
            await b.tasks.rerate(b.vika, task.id, 600, task.version, True)
        else:
            await b.tasks.create(b.max, "Retry", "original")
    assert b.tasks.edit_snapshot(task) == before
    assert [len(b.session.of(model)) for model in (AuditEvent, Notification, ScoreEvent)] == counts


@pytest.mark.parametrize("action", ["view", "edit", "disable", "task_create", "list", "count"])
async def test_rogue_template_cannot_supply_approval_or_be_read_or_mutated(business, action):
    b = business
    service = await template_service(b)
    template = await service.create(b.max, "English", 500, "template", 60)
    template.creator_user_id = 999
    counts = [len(b.session.of(model)) for model in (AuditEvent, Notification, ScoreEvent)]
    if action == "list":
        assert await service.list(b.max) == []
    elif action == "count":
        assert await service.count(b.max) == 0
    else:
        with pytest.raises(DomainError):
            if action == "view":
                await service.view(b.max, template.id)
            elif action == "edit":
                await service.edit(b.vika, template.id, template.version, points=700)
            elif action == "disable":
                await service.disable(b.max, template.id, template.version)
            else:
                await b.tasks.create(
                    b.max,
                    "English",
                    "rogue-auto",
                    estimate=60,
                    proposed_points=500,
                    template_id=template.id,
                    template_version=template.version,
                )
    assert template.points == 500 and template.is_active
    assert [len(b.session.of(model)) for model in (AuditEvent, Notification, ScoreEvent)] == counts


async def test_mismatched_configured_user_roles_reject_create(business):
    business.vika.role = "rogue"
    with pytest.raises(DomainError):
        await business.tasks.create(business.max, "Task", "bad-role")
