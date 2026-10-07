from sqlalchemy import func, select

from app.models import TaskTemplate
from app.repositories.base import Repository
from app.services.rules import DomainError


class TaskTemplateRepository(Repository):
    async def list(
        self, active_only: bool = True, page: int = 0, *, creator_ids: frozenset[int] | None = None
    ) -> list[TaskTemplate]:
        if type(page) is not int or not 0 <= page <= 100000:
            raise DomainError("Недопустимая страница.")
        query = select(TaskTemplate).order_by(TaskTemplate.id).offset(page * 5).limit(5)
        if creator_ids is not None:
            query = query.where(TaskTemplate.creator_user_id.in_(creator_ids))
        if active_only:
            query = query.where(TaskTemplate.is_active.is_(True))
        return list(await self.session.scalars(query))

    async def count(
        self, active_only: bool = True, *, creator_ids: frozenset[int] | None = None
    ) -> int:
        query = select(func.count(TaskTemplate.id))
        if creator_ids is not None:
            query = query.where(TaskTemplate.creator_user_id.in_(creator_ids))
        if active_only:
            query = query.where(TaskTemplate.is_active.is_(True))
        return int(await self.session.scalar(query) or 0)

    async def by_operation(self, key: str) -> TaskTemplate | None:
        return await self.session.scalar(
            select(TaskTemplate).where(TaskTemplate.operation_key == key)
        )
