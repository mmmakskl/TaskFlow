from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import User
from app.services.base import AuthorizedService
from app.services.rules import DomainError, positive_integer
from app.services.transactions import atomic


class SettingsService(AuthorizedService):
    @atomic
    async def change(self, actor: User, field: str, value: str) -> None:
        actor = await self.actor(actor)
        if not isinstance(value, str) or len(value) > 64:
            raise DomainError("Недопустимое значение настройки.")
        user = await self.repo.get(User, actor.id, lock=True)
        if field == "weekly_target":
            try:
                parsed = int(value)
            except ValueError:
                raise DomainError("Введи целое число от 1 до 1 000 000.") from None
            new: object = positive_integer(parsed, 1000000)
        elif field == "timezone":
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
        await self.repo.flush()
