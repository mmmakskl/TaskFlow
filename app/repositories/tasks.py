from sqlalchemy import func, select

from app.models import Task, User
from app.repositories.base import Repository
from app.services.rules import DomainError

FILTERS = {
    "active": ("BACKLOG", "TODO", "IN_PROGRESS"),
    "work": ("IN_PROGRESS",),
    "backlog": ("BACKLOG",),
    "done": ("DONE",),
    "archive": ("CANCELLED",),
}


class TaskRepository(Repository):
    async def list(
        self, actor: User, filter_name: str = "active", page: int = 0, pending: bool = False
    ) -> tuple[list[Task], int]:
        if page < 0 or page > 100000 or filter_name not in FILTERS:
            raise DomainError("Недопустимая страница.")
        scope = Task.evaluator_user_id == actor.id if pending else Task.owner_user_id == actor.id
        statuses = ("BACKLOG",) if pending else FILTERS[filter_name]
        conditions = (scope, Task.deleted_at.is_(None), Task.status.in_(statuses))
        count = int(await self.session.scalar(select(func.count(Task.id)).where(*conditions)) or 0)
        page = min(page, max(0, (count - 1) // 5))
        rows = await self.session.scalars(
            select(Task).where(*conditions).order_by(Task.id.desc()).offset(page * 5).limit(5)
        )
        return list(rows), count

    async def pending_next(self, actor: User, after_id: int = 0) -> tuple[Task | None, int]:
        conditions = (
            Task.evaluator_user_id == actor.id,
            Task.status == "BACKLOG",
            Task.deleted_at.is_(None),
        )
        count = int(await self.session.scalar(select(func.count(Task.id)).where(*conditions)) or 0)
        row = await self.session.scalar(
            select(Task).where(*conditions, Task.id > after_id).order_by(Task.id).limit(1)
        )
        if row is None:
            row = await self.session.scalar(
                select(Task).where(*conditions).order_by(Task.id).limit(1)
            )
        return row, count

    async def by_operation(self, key: str) -> Task | None:
        return await self.session.scalar(select(Task).where(Task.operation_key == key))
