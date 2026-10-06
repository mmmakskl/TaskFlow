from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import User
from app.repositories.base import Repository
from app.services.rules import DomainError, positive_integer


class SettingsService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def change(self, actor: User, field: str, value: str) -> None:
        user = await self.repo.get(User, actor.id, lock=True)
        if field == "weekly_target":
            try:
                parsed = int(value)
            except ValueError:
                raise DomainError("Введи целое число от 1 до 1 000 000.") from None
            new: object = positive_integer(parsed, 1000000)
        elif field == "timezone":
            if len(value) > 64:
                raise DomainError("Часовой пояс слишком длинный.")
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise DomainError("Введи часовой пояс, например Europe/Helsinki.") from None
            new = value
        else:
            raise DomainError("Недопустимая настройка.")
        old = getattr(user, field)
        setattr(user, field, new)
        self.repo.audit(actor, user, "settings", {field: old}, {field: new})
        await self.repo.session.flush()
