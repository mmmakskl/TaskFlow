import pytest

from app.services.rules import DomainError, operation_key, text_field


@pytest.mark.parametrize("value", ["a\x00b", "a\ud800b", "a\udfffb"])
def test_invalid_postgres_text_is_rejected(value):
    with pytest.raises(DomainError):
        text_field(value, 200, "Название")
    with pytest.raises(DomainError):
        operation_key(value)


@pytest.mark.parametrize(
    "zone", ["Europe/Helsinki", "Europe/Moscow", "America/New_York", "Pacific/Kiritimati"]
)
@pytest.mark.parametrize("date", ["2026-03-23", "2026-10-19", "2026-10-26"])
def test_independent_local_week_oracle(zone, date):
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    from app.utils.time import week_bounds

    monday = datetime.combine(datetime.fromisoformat(date).date(), time.min, ZoneInfo(zone))
    next_monday = monday + timedelta(days=7)
    sunday = next_monday - timedelta(microseconds=1)
    for instant in (monday, monday + timedelta(days=3), sunday):
        start, end = week_bounds(instant, zone)
        assert start == monday and end == next_monday
    assert week_bounds(next_monday, zone)[0] == next_monday
