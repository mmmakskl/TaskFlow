from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.models import ScoreEvent, Task, TaskTemplate, User
from app.repositories.base import Repository
from app.services.rules import DomainError
from app.utils.time import utcnow


@dataclass(frozen=True)
class TaskSummary:
    todo_points: int
    in_progress_points: int
    pending_count: int
    completed_points: int

    @property
    def planned_points(self) -> int:
        return self.todo_points + self.in_progress_points


FILTERS = {
    "all": ("BACKLOG", "TODO", "IN_PROGRESS", "DONE", "CANCELLED"),
    "active": ("BACKLOG", "TODO", "IN_PROGRESS"),
    "work": ("IN_PROGRESS",),
    "backlog": ("BACKLOG",),
    "done": ("DONE",),
    "archive": ("CANCELLED",),
    "overdue": ("BACKLOG", "TODO", "IN_PROGRESS", "DONE"),
}


class TaskRepository(Repository):
    async def list(
        self,
        actor: User,
        filter_name: str = "all",
        page: int = 0,
        pending: bool = False,
        scope: str = "mine",
    ) -> tuple[list[Task], int]:
        if type(page) is not int or not 0 <= page <= 100000 or filter_name not in FILTERS:
            raise DomainError("Недопустимая страница.")
        scope_condition = (
            Task.evaluator_user_id == actor.id if pending else self.scope(actor, scope)
        )
        statuses = ("BACKLOG",) if pending else FILTERS[filter_name]
        conditions: tuple[ColumnElement[bool], ...] = (
            scope_condition,
            Task.deleted_at.is_(None),
            Task.status.in_(statuses),
            self.visible(),
        )
        if filter_name == "overdue" and not pending:
            conditions += (
                Task.deadline.is_not(None),
                or_(
                    (Task.status == "DONE") & (Task.completed_at > Task.deadline),
                    (Task.status.in_(("BACKLOG", "TODO", "IN_PROGRESS")))
                    & (Task.deadline < utcnow()),
                ),
            )
        count = int(await self.session.scalar(select(func.count(Task.id)).where(*conditions)) or 0)
        page = min(page, max(0, (count - 1) // 5))
        rows = await self.session.scalars(
            select(Task).where(*conditions).order_by(Task.id.desc()).offset(page * 5).limit(5)
        )
        return list(rows), count

    @staticmethod
    def visible() -> ColumnElement[bool]:
        participants = select(User.id).where(User.role.in_(("max", "vika")))
        return (
            Task.creator_user_id.in_(participants)
            & Task.owner_user_id.in_(participants)
            & Task.evaluator_user_id.in_(participants)
            & Task.proposal_by_user_id.in_(participants)
            & or_(Task.rated_by_user_id.is_(None), Task.rated_by_user_id.in_(participants))
            & or_(Task.shared_user_id.is_(None), Task.shared_user_id.in_(participants))
            & or_(
                Task.template_id.is_(None),
                Task.template_id.in_(
                    select(TaskTemplate.id).where(TaskTemplate.creator_user_id.in_(participants))
                ),
            )
        )

    def scope(self, actor: User, name: str) -> ColumnElement[bool]:
        mine = or_(Task.owner_user_id == actor.id, Task.shared_user_id == actor.id)
        if name == "mine":
            return mine
        if name == "peer":
            return or_(Task.owner_user_id != actor.id, Task.kind == "SHARED")
        if name == "shared":
            return Task.kind == "SHARED"
        if name == "assigned":
            return (Task.creator_user_id == actor.id) & (Task.owner_user_id != actor.id)
        if name == "all":
            return Task.id.is_not(None)
        raise DomainError("Недопустимый список.")

    async def summary(self, actor: User, scope: str = "mine") -> TaskSummary:
        conditions = (self.scope(actor, scope), Task.deleted_at.is_(None), self.visible())
        totals = []
        for status in ("TODO", "IN_PROGRESS"):
            totals.append(
                int(
                    await self.session.scalar(
                        select(func.coalesce(func.sum(Task.rated_points), 0)).where(
                            *conditions, Task.status == status
                        )
                    )
                    or 0
                )
            )
        pending = int(
            await self.session.scalar(
                select(func.count(Task.id)).where(*conditions, Task.status == "BACKLOG")
            )
            or 0
        )
        user_id = actor.id
        if scope == "peer":
            user_id = int(
                await self.session.scalar(
                    select(User.id).where(User.id != actor.id, User.role.in_(("max", "vika")))
                )
                or 0
            )
        completed = int(
            await self.session.scalar(
                select(func.coalesce(func.sum(ScoreEvent.points), 0)).where(
                    ScoreEvent.user_id == user_id, ScoreEvent.category == "task"
                )
            )
            or 0
        )
        return TaskSummary(totals[0], totals[1], pending, completed)

    async def pending_next(self, actor: User, after_id: int = 0) -> tuple[Task | None, int]:
        if type(after_id) is not int or not 0 <= after_id <= 2147483647:
            raise DomainError("Недопустимая позиция списка.")
        conditions = (
            self.visible(),
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

    async def enqueue_rating(self, evaluator_id: int, debounce_seconds: int) -> None:
        from app.repositories.notifications import NotificationRepository

        await NotificationRepository(self.session).enqueue_rating(evaluator_id, debounce_seconds)
