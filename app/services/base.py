from app.config.settings import Settings
from app.models import User
from app.repositories.base import Repository
from app.repositories.users import UserRepository
from app.services.rules import DomainError


class AuthorizedService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.user_repo = UserRepository(repo.session, settings)

    async def actor(self, actor: User) -> User:
        return await self.user_repo.authenticate(actor)

    async def configured_participant_ids(self) -> frozenset[int]:
        participants = await self.user_repo.participants()
        expected_roles = {
            self.settings.user_max_id: "max",
            self.settings.user_vika_id: "vika",
        }
        if any(user.role != expected_roles.get(user.telegram_id) for user in participants):
            raise DomainError("Не удалось определить двух участников. Проверь конфигурацию.")
        return frozenset(user.id for user in participants)
