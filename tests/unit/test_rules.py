from datetime import UTC, datetime

import pytest

from app.models.entities import CalculationType, TaskStatus
from app.services.rules import DomainError, penalty_points, rating_points, transition
from app.utils.time import week_bounds


@pytest.mark.parametrize("points", [1, 250, 10000])
def test_valid_rating(points: int) -> None:
    assert rating_points(points) == points


@pytest.mark.parametrize("points", [0, -1, 10001, True, 1.5, "250"])
def test_invalid_rating(points: object) -> None:
    with pytest.raises(DomainError):
        rating_points(points)


@pytest.mark.parametrize(
    "kind,rate,quantity,manual,expected",
    [
        (CalculationType.PER_MINUTE, 100, 12, None, 1200),
        (CalculationType.FIXED, 700, None, None, 700),
        (CalculationType.MANUAL, 0, None, 900, 900),
    ],
)
def test_penalty_calculations(
    kind: str, rate: int, quantity: int | None, manual: int | None, expected: int
) -> None:
    assert penalty_points(kind, rate, quantity, manual) == expected


@pytest.mark.parametrize(
    "old,new,confirm",
    [
        (TaskStatus.TODO, TaskStatus.IN_PROGRESS, False),
        (TaskStatus.TODO, TaskStatus.DONE, False),
        (TaskStatus.IN_PROGRESS, TaskStatus.DONE, False),
        (TaskStatus.DONE, TaskStatus.TODO, True),
        (TaskStatus.DONE, TaskStatus.IN_PROGRESS, True),
        (TaskStatus.BACKLOG, TaskStatus.CANCELLED, True),
    ],
)
def test_transitions(old: str, new: str, confirm: bool) -> None:
    transition(old, new, confirm)


@pytest.mark.parametrize(
    "old,new,confirm",
    [
        (TaskStatus.BACKLOG, TaskStatus.DONE, True),
        (TaskStatus.DONE, TaskStatus.TODO, False),
        (TaskStatus.CANCELLED, TaskStatus.DONE, True),
        (TaskStatus.TODO, TaskStatus.CANCELLED, False),
    ],
)
def test_invalid_transitions(old: str, new: str, confirm: bool) -> None:
    with pytest.raises(DomainError):
        transition(old, new, confirm)


def test_week_uses_local_monday_and_exclusive_end() -> None:
    start, end = week_bounds(datetime(2026, 10, 4, 21, 30, tzinfo=UTC), "Europe/Helsinki")
    assert start == datetime(2026, 10, 4, 21, tzinfo=UTC)
    assert end == datetime(2026, 10, 11, 21, tzinfo=UTC)


@pytest.mark.parametrize(
    "at,hours",
    [
        (datetime(2026, 3, 25, tzinfo=UTC), 167),
        (datetime(2026, 10, 21, tzinfo=UTC), 169),
    ],
)
def test_dst_week_is_calendar_week(at: datetime, hours: int) -> None:
    start, end = week_bounds(at, "Europe/Helsinki")
    assert (end - start).total_seconds() == hours * 3600


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        week_bounds(datetime(2026, 10, 1), "Europe/Helsinki")


@pytest.mark.parametrize("value", [None, 1, 90, 10080])
def test_estimate_range(value):
    from app.services.rules import estimated_minutes

    assert estimated_minutes(value) == value


@pytest.mark.parametrize("value", [0, -1, 10081, True, 1.5, "30"])
def test_estimate_invalid(value):
    from app.services.rules import estimated_minutes

    with pytest.raises(DomainError):
        estimated_minutes(value)


@pytest.mark.parametrize(
    "value,label", [(5, "5 мин"), (60, "1 ч"), (95, "1 ч 35 мин"), (120, "2 ч")]
)
def test_duration_label(value, label):
    from app.utils.duration import duration

    assert duration(value) == label
