from datetime import UTC, datetime, timedelta

import pytest

from app.models import AuditEvent, ScoreEvent
from app.services.rules import DomainError
from app.utils.time import week_bounds
from tests.business_fakes import NOW


@pytest.mark.parametrize(
    "zone,at,hours",
    [
        ("Europe/Helsinki", datetime(2026, 3, 25, tzinfo=UTC), 167),
        ("Europe/Helsinki", datetime(2026, 10, 21, tzinfo=UTC), 169),
        ("Europe/Moscow", NOW, 168),
        ("UTC", NOW, 168),
    ],
)
def test_week_dst_and_local_midnight(zone, at, hours):
    from zoneinfo import ZoneInfo

    start, end = week_bounds(at, zone)
    assert (end - start).total_seconds() == hours * 3600
    for bound in (start, end):
        local = bound.astimezone(ZoneInfo(zone))
        assert local.weekday() == 0 and local.hour == local.minute == local.second == 0
    assert week_bounds(end, zone)[0] == end
    assert week_bounds(start - timedelta(microseconds=1), zone)[1] == start


async def test_result_half_open_bounds_and_negative_score(business):
    b = business
    start, end = week_bounds(NOW, b.settings.default_timezone)
    for i, (at, category, points) in enumerate(
        [
            (start - timedelta(microseconds=1), "task", 999),
            (start, "task", 500),
            (end - timedelta(microseconds=1), "penalty", -1200),
            (end, "task", 999),
        ]
    ):
        b.session.add(
            ScoreEvent(
                user_id=1, category=category, points=points, effective_at=at, operation_key=f"e{i}"
            )
        )
    await b.session.flush()
    scores = (await b.results.week())[1]
    assert scores[0][1:] == (500, 1200) and scores[0][1] - scores[0][2] == -700
    assert scores[1][1:] == (0, 0)


async def test_history_pagination_and_empty_weeks(business):
    b = business
    assert await b.results.history(99) == ([], 0, False)
    start, _ = week_bounds(NOW, b.settings.default_timezone, -12)
    b.session.add(
        ScoreEvent(
            user_id=1, category="task", points=600, effective_at=start, operation_key="history"
        )
    )
    await b.session.flush()
    first, page, more = await b.results.history(0)
    assert len(first) == 5 and page == 0 and more
    assert all(row[1][0][1:] == (0, 0) for row in first)
    last, page, more = await b.results.history(99)
    assert len(last) == 2 and page == 2 and not more and last[-1][1][0][1:] == (600, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("weekly_target", "0"),
        ("weekly_target", "1000001"),
        ("weekly_target", "abc"),
        ("weekly_target", None),
        ("timezone", "Wrong/Zone"),
        ("timezone", "../UTC"),
        ("timezone", "x" * 65),
        ("timezone", None),
        ("unknown", "1"),
    ],
)
async def test_invalid_settings_do_not_mutate(business, field, value):
    b = business
    with pytest.raises(DomainError):
        await b.config.change(b.max, field, value)
    assert b.max.weekly_target == 10000 and b.max.timezone == b.settings.default_timezone
    assert not b.session.of(AuditEvent)


async def test_valid_settings_and_audit(business):
    b = business
    await b.config.change(b.max, "weekly_target", "20000")
    await b.config.change(b.max, "timezone", "Europe/Moscow")
    assert b.max.weekly_target == 20000 and b.max.timezone == "Europe/Moscow"
    assert b.vika.weekly_target == 10000
    events = b.session.of(AuditEvent)
    assert len(events) == 2 and events[0].old_data == {"weekly_target": 10000}


@pytest.mark.parametrize("value", [True, 1.5, "1", None])
async def test_invalid_result_pagination(business, value):
    with pytest.raises(DomainError):
        await business.results.history(value)
    with pytest.raises(DomainError):
        await business.results.week(value)
