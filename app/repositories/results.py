from datetime import datetime

from sqlalchemy import func, select

from app.models import ScoreEvent
from app.repositories.base import Repository


class ResultRepository(Repository):
    async def totals(self, start: datetime, end: datetime) -> dict[int, dict[str, int]]:
        rows = await self.session.execute(
            select(ScoreEvent.user_id, ScoreEvent.category, func.sum(ScoreEvent.points))
            .where(ScoreEvent.effective_at >= start, ScoreEvent.effective_at < end)
            .group_by(ScoreEvent.user_id, ScoreEvent.category)
        )
        totals: dict[int, dict[str, int]] = {}
        for user_id, category, points in rows:
            totals.setdefault(user_id, {})[category] = int(points)
        return totals

    async def earliest(self) -> datetime | None:
        return await self.session.scalar(select(func.min(ScoreEvent.effective_at)))

    async def weekly_totals(
        self, start: datetime, end: datetime, timezone: str
    ) -> dict[tuple[str, int, str], int]:
        week = func.date_trunc("week", func.timezone(timezone, ScoreEvent.effective_at))
        rows = await self.session.execute(
            select(week, ScoreEvent.user_id, ScoreEvent.category, func.sum(ScoreEvent.points))
            .where(ScoreEvent.effective_at >= start, ScoreEvent.effective_at < end)
            .group_by(week, ScoreEvent.user_id, ScoreEvent.category)
        )
        return {
            (date.date().isoformat(), user_id, category): int(points)
            for date, user_id, category, points in rows
        }
