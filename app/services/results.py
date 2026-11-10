from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import User
from app.repositories.results import ResultRepository
from app.services.base import AuthorizedService
from app.services.rules import DomainError
from app.utils.time import utcnow, week_bounds, week_label


class ResultService(AuthorizedService):
    repo: ResultRepository

    async def week(
        self, offset: int = 0, at: datetime | None = None
    ) -> tuple[str, list[tuple[User, int, int]]]:
        if type(offset) is not int or not -5200 <= offset <= 5200:
            raise DomainError("Недопустимая неделя.")
        start, end = week_bounds(at or utcnow(), self.settings.default_timezone, offset)
        totals = await self.repo.totals(start, end)
        users = await self.user_repo.participants()
        scores = [
            (
                user,
                totals.get(user.id, {}).get("task", 0),
                -totals.get(user.id, {}).get("penalty", 0),
            )
            for user in users
        ]
        return week_label(start, end, self.settings.default_timezone), scores

    async def trend(
        self, weeks: int = 5, at: datetime | None = None
    ) -> list[tuple[str, str, list[tuple[User, int, int]]]]:
        if type(weeks) is not int or weeks not in (4, 5, 8, 12):
            raise DomainError("Недопустимый период статистики.")
        timezone = self.settings.default_timezone
        now = at or utcnow()
        bounds = [week_bounds(now, timezone, offset) for offset in range(1 - weeks, 1)]
        totals = await self.repo.weekly_totals(bounds[0][0], bounds[-1][1], timezone)
        users = await self.user_repo.participants()
        output = []
        for start, end in bounds:
            date = start.astimezone(ZoneInfo(timezone)).date().isoformat()
            scores = [
                (
                    user,
                    totals.get((date, user.id, "task"), 0),
                    -totals.get((date, user.id, "penalty"), 0),
                )
                for user in users
            ]
            output.append((date, week_label(start, end, timezone), scores))
        return output

    async def history(
        self, page: int
    ) -> tuple[list[tuple[str, list[tuple[User, int, int]]]], int, bool]:
        if type(page) is not int:
            raise DomainError("Недопустимая страница.")
        timezone = self.settings.default_timezone
        zone = ZoneInfo(timezone)
        now = utcnow()
        current_start, _ = week_bounds(now, timezone)
        earliest = await self.repo.earliest()
        if earliest is None or earliest >= current_start:
            return [], 0, False
        first_start, _ = week_bounds(earliest, timezone)
        weeks = (
            current_start.astimezone(zone).date() - first_start.astimezone(zone).date()
        ).days // 7
        page = max(0, min((weeks - 1) // 5, page))
        offsets = list(range(page * 5 + 1, min(weeks, page * 5 + 5) + 1))
        bounds = [week_bounds(now, timezone, -offset) for offset in offsets]
        totals = await self.repo.weekly_totals(bounds[-1][0], bounds[0][1], timezone)
        users = await self.user_repo.participants()
        output = []
        for start, end in bounds:
            date = start.astimezone(zone).date().isoformat()
            scores = [
                (
                    user,
                    totals.get((date, user.id, "task"), 0),
                    -totals.get((date, user.id, "penalty"), 0),
                )
                for user in users
            ]
            output.append((week_label(start, end, timezone), scores))
        return output, page, weeks > (page + 1) * 5
