from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models import User
from app.repositories.base import Repository
from app.services.rules import DomainError, authenticated_identity


class UserRepository(Repository):
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        super().__init__(session)
        self.settings = settings

    async def authenticate(self, actor: User) -> User:
        if actor.telegram_id not in self.settings.authorized_ids:
            raise DomainError("🔒 Доступ запрещён.")
        persisted = await self.user(actor.telegram_id)
        authenticated_identity(
            actor.id,
            actor.telegram_id,
            persisted.id,
            persisted.telegram_id,
            self.settings.authorized_ids,
        )
        return persisted

    async def participants(self) -> list[User]:
        users = list(
            await self.session.scalars(
                select(User)
                .where(User.telegram_id.in_(self.settings.authorized_ids))
                .order_by(User.id)
            )
        )
        if len(users) != 2:
            raise DomainError("Не удалось определить двух участников. Проверь конфигурацию.")
        return users

    async def peer(self, actor: User) -> User:
        peers = [user for user in await self.participants() if user.id != actor.id]
        if len(peers) != 1:
            raise DomainError("Не удалось определить второго пользователя.")
        return peers[0]
