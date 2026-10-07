from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models import Task
from app.services.deadlines import deadline_delay, delay_label

AT = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "status,deadline,completed,expected",
    [
        ("TODO", None, None, None),
        ("TODO", AT, None, None),
        ("TODO", AT + timedelta(seconds=1), None, None),
        ("TODO", AT - timedelta(seconds=1), None, timedelta(seconds=1)),
        ("BACKLOG", AT - timedelta(days=1), None, timedelta(days=1)),
        ("IN_PROGRESS", AT - timedelta(hours=2), None, timedelta(hours=2)),
        ("DONE", AT, AT, None),
        ("DONE", AT, AT - timedelta(seconds=1), None),
        ("DONE", AT - timedelta(days=2), AT - timedelta(days=1), timedelta(days=1)),
        ("CANCELLED", AT - timedelta(days=1), None, None),
    ],
)
def test_deadline_boundary_and_completion_freezes_delay(status, deadline, completed, expected):
    task = Task(status=status, deadline=deadline, completed_at=completed)
    assert deadline_delay(task, AT) == expected


def test_elapsed_time_across_dst_fallback_uses_instants():
    zone = ZoneInfo("Europe/Helsinki")
    task = Task(status="TODO", deadline=datetime(2026, 10, 25, 3, 30, tzinfo=zone, fold=0))
    assert deadline_delay(task, datetime(2026, 10, 25, 3, 30, tzinfo=zone, fold=1)) == timedelta(
        hours=1
    )


@pytest.mark.parametrize(
    "seconds,label", [(1, "меньше минуты"), (60, "1 мин"), (3600, "1 ч"), (90061, "1 д 1 ч 1 мин")]
)
def test_delay_labels(seconds, label):
    assert delay_label(timedelta(seconds=seconds)) == label
