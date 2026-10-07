import logging
from datetime import datetime

from app.config.settings import Settings
from app.models import Notification, ScoreEvent, Task, User
from app.repositories.tasks import TaskRepository
from app.services.base import AuthorizedService
from app.services.rules import (
    DomainError,
    StaleAction,
    estimated_minutes,
    operation_key,
    rating_points,
    text_field,
    transition,
)
from app.services.transactions import atomic
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


class TaskService(AuthorizedService):
    repo: TaskRepository

    def __init__(self, repo: TaskRepository, settings: Settings) -> None:
        super().__init__(repo, settings)
        self.repo = repo

    @atomic
    async def create(
        self,
        actor: User,
        title: str,
        key: str,
        comment: str | None = None,
        deadline: datetime | None = None,
        estimate: int | None = None,
    ) -> Task:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(key)
        existing = await self.repo.by_operation(key)
        if existing:
            if existing.owner_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing
        title = text_field(title, 200, "Название")
        if comment is not None:
            comment = text_field(comment, 1500, "Комментарий")
        if deadline is not None and (not isinstance(deadline, datetime) or deadline.tzinfo is None):
            raise DomainError("Дедлайн должен включать часовой пояс.")
        peer = await self.user_repo.peer(actor)
        task = Task(
            owner_user_id=actor.id,
            evaluator_user_id=peer.id,
            title=title,
            comment=comment,
            deadline=deadline,
            estimated_minutes=estimated_minutes(estimate),
            operation_key=key,
        )
        self.repo.add(task)
        await self.repo.flush()
        self.repo.audit(
            actor, task, "create", {}, {"title": title, "estimated_minutes": task.estimated_minutes}
        )
        await self.repo.enqueue_rating(
            task.evaluator_user_id, self.settings.rating_notification_debounce_seconds
        )
        logger.info("task_created entity_id=%s actor_id=%s", task.id, actor.id)
        return task

    async def view(self, actor: User, task_id: int) -> Task:
        actor = await self.actor(actor)
        task = await self.repo.get(Task, task_id)
        if actor.id not in (task.owner_user_id, task.evaluator_user_id) or task.deleted_at:
            raise DomainError("Эта задача недоступна.")
        return task

    @atomic
    async def rate(self, actor: User, task_id: int, points: int, version: int) -> Task:
        actor = await self.actor(actor)
        points = rating_points(points)
        task = await self.repo.get(Task, task_id, lock=True)
        if task.deleted_at:
            raise StaleAction()
        if task.evaluator_user_id != actor.id or task.owner_user_id == actor.id:
            raise DomainError("Свою задачу оценивать нельзя.")
        self.repo.version(task, version)
        if task.status != "BACKLOG" or task.rated_points is not None:
            raise DomainError("Эта задача уже оценена или отменена.")
        task.rated_points, task.rated_by_user_id, task.rated_at = points, actor.id, utcnow()
        task.status = "TODO"
        task.version += 1
        self.repo.audit(actor, task, "rate", {"rated_points": None}, {"rated_points": points})
        self.repo.add(
            Notification(
                recipient_user_id=task.owner_user_id,
                kind="rated",
                payload={
                    "task_id": task.id,
                    "title": task.title,
                    "points": points,
                    "evaluator": actor.display_name,
                },
                dedupe_key=f"rated:{task.id}",
                available_at=utcnow(),
            )
        )
        await self.repo.flush()
        return task

    @atomic
    async def change_status(
        self, actor: User, task_id: int, status: str, version: int, confirmed: bool = False
    ) -> Task:
        actor = await self.actor(actor)
        task = await self.repo.get(Task, task_id, lock=True)
        if task.deleted_at or task.owner_user_id != actor.id:
            raise DomainError("Статус меняет только владелец задачи.")
        self.repo.version(task, version)
        transition(task.status, status, confirmed)
        old = {
            "status": task.status,
            "awarded_points": task.awarded_points,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }
        if task.status == "DONE":
            assert task.completed_at is not None and task.awarded_points is not None
            self.repo.add(
                ScoreEvent(
                    user_id=actor.id,
                    category="task",
                    points=-task.awarded_points,
                    effective_at=task.completed_at,
                    operation_key=f"task:{task.id}:reopen:{task.version}",
                )
            )
            task.completed_at = None
            task.awarded_points = None
        if status == "DONE":
            if task.rated_points is None:
                raise DomainError("Сначала второй пользователь должен оценить задачу.")
            task.awarded_points, task.completed_at = task.rated_points, utcnow()
            self.repo.add(
                ScoreEvent(
                    user_id=actor.id,
                    category="task",
                    points=task.awarded_points,
                    effective_at=task.completed_at,
                    operation_key=f"task:{task.id}:done:{task.version}",
                )
            )
        if status == "IN_PROGRESS":
            task.started_at = utcnow()
        if status == "CANCELLED":
            task.cancelled_at = utcnow()
        task.status = status
        task.version += 1
        self.repo.audit(
            actor, task, "status", old, {"status": status, "awarded_points": task.awarded_points}
        )
        await self.repo.flush()
        logger.info("task_status entity_id=%s status=%s", task.id, status)
        return task

    @atomic
    async def edit(self, actor: User, task_id: int, version: int, **changes: object) -> Task:
        actor = await self.actor(actor)
        task = await self.repo.get(Task, task_id, lock=True)
        if (
            task.deleted_at
            or task.owner_user_id != actor.id
            or task.status in ("DONE", "CANCELLED")
        ):
            raise DomainError("Редактировать можно только свою активную задачу.")
        self.repo.version(task, version)
        if not changes:
            raise DomainError("Укажи хотя бы одно изменение.")
        old = self.edit_snapshot(task)
        for field, value in changes.items():
            if field == "title" and isinstance(value, str):
                task.title = text_field(value, 200, "Название")
            elif field == "comment" and (value is None or isinstance(value, str)):
                task.comment = text_field(value, 1500, "Комментарий") if value else None
            elif field == "deadline" and (value is None or isinstance(value, datetime)):
                if value is not None and value.tzinfo is None:
                    raise DomainError("Дата должна содержать часовой пояс.")
                task.deadline = value
            elif field == "estimated_minutes":
                task.estimated_minutes = estimated_minutes(value)
            else:
                raise DomainError("Недопустимое поле.")
        task.version += 1
        self.repo.audit(
            actor,
            task,
            "edit",
            old,
            self.edit_snapshot(task),
        )
        await self.repo.flush()
        return task

    @staticmethod
    def edit_snapshot(task: Task) -> dict[str, object]:
        return {
            "title": task.title,
            "comment": task.comment,
            "deadline": str(task.deadline),
            "estimated_minutes": task.estimated_minutes,
        }
