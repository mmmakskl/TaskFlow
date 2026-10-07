from app.config.settings import Settings
from app.models import User
from app.repositories.base import Repository
from app.repositories.users import UserRepository


class AuthorizedService:
    def __init__(self, repo: Repository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.user_repo = UserRepository(repo.session, settings)

    async def actor(self, actor: User) -> User:
        return await self.user_repo.authenticate(actor)
