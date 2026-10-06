from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def week_bounds(at: datetime, timezone: str, offset: int = 0) -> tuple[datetime, datetime]:
    if at.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    zone = ZoneInfo(timezone)
    local_date = at.astimezone(zone).date()
    monday = local_date - timedelta(days=local_date.weekday()) + timedelta(weeks=offset)
    start = datetime.combine(monday, time.min, zone)
    end = datetime.combine(monday + timedelta(days=7), time.min, zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def week_label(start: datetime, end: datetime, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    return f"{start.astimezone(zone):%d.%m}–{(end.astimezone(zone) - timedelta(days=1)):%d.%m.%Y}"
