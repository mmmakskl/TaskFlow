from copy import deepcopy

import pytest

from app.models import AuditEvent, Penalty, ScoreEvent, User
from app.services.rules import DomainError, StaleAction


@pytest.mark.parametrize(
    "method,rate,quantity,manual,points",
    [
        ("PER_MINUTE", 100, 12, None, 1200),
        ("FIXED", 250, None, None, 250),
        ("MANUAL", 0, None, 725, 725),
        ("PER_MINUTE", 1000000, 1000, None, 1000000000),
        ("MANUAL", 0, None, 1000000000, 1000000000),
    ],
)
async def test_methods_snapshots_scores_and_duplicates(
    business, method, rate, quantity, manual, points
):
    b = business
    await b.penalties.configure(b.vika, 1, 1, calculation_type=method, rate=rate)
    draft = await b.penalties.preview(1, quantity, manual)
    penalty = await b.penalties.create(
        b.vika, b.max.id, draft, "penalty", True, comment=" details "
    )
    assert penalty.points == points and penalty.comment == "details"
    assert penalty.name_snapshot == b.kind.name
    assert penalty.description_snapshot == b.kind.description
    assert penalty.calculation_type_snapshot == method and penalty.rate_snapshot == rate
    assert penalty.quantity == quantity
    assert (await b.results.week())[1][0][1:] == (0, points)
    assert await b.penalties.create(b.vika, b.max.id, draft, "penalty", True) is penalty
    assert len(b.session.of(Penalty)) == len(b.session.of(ScoreEvent)) == 1
    await b.penalties.configure(
        b.max, 1, b.kind.version, name="Changed", description="New", rate=150
    )
    assert penalty.name_snapshot == "Instagram" and penalty.description_snapshot == "Описание"
    assert penalty.rate_snapshot == rate and penalty.points == points
    await b.penalties.delete(b.max, penalty.id, penalty.version, True)
    assert (await b.results.week())[1][0][1:] == (0, 0)
    with pytest.raises(StaleAction):
        await b.penalties.delete(b.max, penalty.id, 1, True)
    with pytest.raises(DomainError):
        await b.penalties.delete(b.max, penalty.id, penalty.version, True)
    assert len(b.session.of(ScoreEvent)) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("points", 999),
        ("rate", 150),
        ("name", "Forged"),
        ("description", "Forged"),
        ("calculation_type", "FIXED"),
        ("type_version", 99),
        ("quantity", True),
        ("type_id", True),
        ("points", 1.5),
        ("extra", 1),
    ],
)
async def test_preview_tampering_has_no_side_effect(business, field, value):
    b = business
    draft = await b.penalties.preview(1, 5, None)
    draft[field] = value
    with pytest.raises(DomainError):
        await b.penalties.create(b.vika, 1, draft, "tampered", True)
    assert not b.session.of(Penalty) and not b.session.of(ScoreEvent)


@pytest.mark.parametrize("confirmed", [False, None, 1, "yes"])
async def test_confirmation_required(business, confirmed):
    b = business
    with pytest.raises(DomainError):
        await b.penalties.create(b.max, 1, await b.penalties.preview(1, 5, None), "key", confirmed)
    assert not b.session.of(Penalty)


async def test_disabled_type_and_stale_preview(business):
    b = business
    draft = await b.penalties.preview(1, 5, None)
    await b.penalties.configure(b.max, 1, 1, is_active=False)
    with pytest.raises(DomainError):
        await b.penalties.preview(1, 5, None)
    with pytest.raises(DomainError):
        await b.penalties.create(b.max, 1, draft, "key", True)
    draft["type_version"] = b.kind.version
    with pytest.raises(DomainError):
        await b.penalties.create(b.max, 1, draft, "key", True)
    assert not b.session.of(Penalty)


async def test_only_authenticated_participants_can_create_or_receive(business):
    b = business
    outsider = User(
        id=3, telegram_id=1003, display_name="Other", role="other", timezone="UTC", weekly_target=1
    )
    b.session.add(outsider)
    await b.session.flush()
    draft = await b.penalties.preview(1, 5, None)
    for actor, target in [(outsider, 1), (b.max, 3), (User(id=2, telegram_id=1001), 1)]:
        with pytest.raises(DomainError):
            await b.penalties.create(actor, target, draft, "key", True)
    penalty = await b.penalties.create(b.max, 2, draft, "key", True)
    with pytest.raises(DomainError):
        await b.penalties.create(b.vika, 2, draft, "key", True)
    with pytest.raises(DomainError):
        await b.penalties.delete(outsider, penalty.id, 1, True)
    with pytest.raises(DomainError):
        await b.penalties.configure(outsider, 1, 1, rate=200)
    assert len(await b.penalties.users()) == 2
    assert len((await b.results.week())[1]) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"unknown": 1},
        {"name": ""},
        {"name": None},
        {"emoji": "x" * 17},
        {"description": "x" * 501},
        {"rate": True},
        {"rate": -1},
        {"rate": 1000001},
        {"calculation_type": "wrong"},
        {"is_active": 1},
        {"rate": 0},
    ],
)
async def test_invalid_configuration_rolls_back(business, changes):
    b = business
    before = (b.kind.name, b.kind.rate, b.kind.version)
    with pytest.raises(DomainError):
        await b.penalties.configure(b.max, 1, 1, **changes)
    assert (b.kind.name, b.kind.rate, b.kind.version) == before
    assert not b.session.of(AuditEvent)


async def test_partial_configuration_failure_restores_fields(business):
    b = business
    with pytest.raises(DomainError):
        await b.penalties.configure(b.max, 1, 1, name="Changed", rate=0)
    assert b.kind.name == "Instagram" and b.kind.rate == 100 and b.kind.version == 1
    with pytest.raises(DomainError):
        await b.penalties.configure(b.max, None, name="New", rate=0, calculation_type="FIXED")
    assert len((await b.penalties.types(True))[0]) == 1


async def test_failed_audit_rolls_back_penalty_and_score(business, monkeypatch):
    b = business
    draft = await b.penalties.preview(1, 5, None)

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(b.penalties.repo, "audit", fail)
    with pytest.raises(RuntimeError):
        await b.penalties.create(b.max, 1, draft, "key", True)
    assert not b.session.of(Penalty) and not b.session.of(ScoreEvent)
    assert b.session.rollbacks == 1


async def test_configuration_changes_affect_only_new_penalty(business):
    b = business
    old = await b.penalties.create(b.vika, 1, await b.penalties.preview(1, 5, None), "old", True)
    snapshot = deepcopy(
        (old.name_snapshot, old.description_snapshot, old.rate_snapshot, old.points)
    )
    await b.penalties.configure(
        b.max, 1, 1, rate=150, name="New name", description="New description"
    )
    new = await b.penalties.create(b.max, 1, await b.penalties.preview(1, 5, None), "new", True)
    assert snapshot == (old.name_snapshot, old.description_snapshot, old.rate_snapshot, old.points)
    assert new.rate_snapshot == 150 and new.points == 750 and new.name_snapshot == "New name"
