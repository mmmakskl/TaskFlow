import logging
from datetime import datetime

from app.config.settings import Settings
from app.models import Notification, ScoreEvent, Task, TaskTarget, TaskTemplate, User
from app.repositories.tasks import TaskRepository
from app.services.base import AuthorizedService
from app.services.deadlines import deadline_delay
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

    async def validate_participants(self, task: Task) -> None:
        allowed = await self.configured_participant_ids()
        identities = (
            task.creator_user_id,
            task.owner_user_id,
            task.shared_user_id,
            task.proposal_by_user_id,
            task.evaluator_user_id,
            task.rated_by_user_id,
        )
        if any(identity not in allowed for identity in identities if identity is not None):
            raise DomainError("Эта задача недоступна.")
        if (
            task.creator_user_id is None
            or task.owner_user_id is None
            or task.proposal_by_user_id is None
            or task.evaluator_user_id is None
            or task.evaluator_user_id == task.proposal_by_user_id
            or (
                task.rated_by_user_id is not None
                and task.rated_by_user_id != task.evaluator_user_id
            )
            or (task.kind == "PERSONAL" and task.shared_user_id is not None)
            or (task.kind == "SHARED" and frozenset(task.participant_user_ids) != allowed)
            or task.kind not in ("PERSONAL", "SHARED")
        ):
            raise DomainError("Эта задача недоступна.")
        if task.template_id is not None:
            template = await self.repo.get(TaskTemplate, task.template_id)
            if template.creator_user_id not in allowed:
                raise DomainError("Этот шаблон недоступен.")

    @staticmethod
    def can_manage(task: Task, actor: User) -> bool:
        if task.first_accepted_at is None:
            return task.creator_user_id == actor.id
        return actor.id in task.participant_user_ids

    @atomic
    async def create(
        self,
        actor: User,
        title: str,
        key: str,
        comment: str | None = None,
        deadline: datetime | None = None,
        estimate: int | None = None,
        *,
        target: TaskTarget = TaskTarget.SELF,
        proposed_points: int | None = None,
        template_id: int | None = None,
        template_version: int | None = None,
    ) -> Task:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(key)
        existing = await self.repo.by_operation(key)
        if existing:
            await self.validate_participants(existing)
            if existing.creator_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing
        title = text_field(title, 200, "Название")
        if comment is not None:
            comment = text_field(comment, 1500, "Комментарий")
        if deadline is not None and (not isinstance(deadline, datetime) or deadline.tzinfo is None):
            raise DomainError("Дедлайн должен включать часовой пояс.")
        allowed = await self.configured_participant_ids()
        peer = await self.user_repo.peer(actor)
        try:
            target = TaskTarget(target)
        except (ValueError, TypeError) as exc:
            raise DomainError("Недопустимый исполнитель.") from exc
        proposal = rating_points(proposed_points) if proposed_points is not None else None
        template = None
        if template_id is not None:
            template = await self.repo.get(TaskTemplate, template_id, lock=True)
            if template.creator_user_id not in allowed:
                raise DomainError("Этот шаблон недоступен.")
            if type(template_version) is not int:
                raise StaleAction()
            self.repo.version(template, template_version)
            if not template.is_active:
                raise StaleAction()
        elif template_version is not None:
            raise DomainError("Не указан шаблон.")
        auto = bool(
            template
            and title == template.title
            and estimate == template.estimated_minutes
            and proposal == template.points
            and target != TaskTarget.PEER
        )
        task = Task(
            creator_user_id=actor.id,
            owner_user_id=peer.id if target == TaskTarget.PEER else actor.id,
            shared_user_id=peer.id if target == TaskTarget.SHARED else None,
            kind="SHARED" if target == TaskTarget.SHARED else "PERSONAL",
            evaluator_user_id=peer.id,
            proposal_by_user_id=actor.id,
            proposed_points=proposal,
            template_id=template.id if template else None,
            template_version=template.version if template else None,
            template_snapshot=(
                {
                    "title": template.title,
                    "estimated_minutes": template.estimated_minutes,
                    "points": template.points,
                }
                if template
                else None
            ),
            status="TODO" if auto else "BACKLOG",
            rated_points=proposal if auto else None,
            rating_source="TEMPLATE" if auto else None,
            rated_at=utcnow() if auto else None,
            first_accepted_at=utcnow() if auto else None,
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
        if not auto:
            await self.repo.enqueue_rating(
                task.evaluator_user_id, self.settings.rating_notification_debounce_seconds
            )
        logger.info("task_created entity_id=%s actor_id=%s", task.id, actor.id)
        return task

    async def view(self, actor: User, task_id: int) -> Task:
        actor = await self.actor(actor)
        task = await self.repo.get(Task, task_id)
        await self.validate_participants(task)
        if (
            actor.id
            not in (
                task.creator_user_id,
                task.owner_user_id,
                task.evaluator_user_id,
                task.shared_user_id,
            )
            or task.deleted_at
        ):
            raise DomainError("Эта задача недоступна.")
        return task

    async def deadline_penalty_task(self, actor: User, task_id: int, version: int) -> Task:
        task = await self.view(actor, task_id)
        self.repo.version(task, version)
        if deadline_delay(task, utcnow()) is None:
            raise DomainError("У этой задачи нет просрочки. Обнови карточку.")
        return task

    @atomic
    async def rate(self, actor: User, task_id: int, points: int, version: int) -> Task:
        actor = await self.actor(actor)
        points = rating_points(points)
        task = await self.repo.get(Task, task_id, lock=True)
        await self.validate_participants(task)
        if task.deleted_at:
            raise StaleAction()
        if task.evaluator_user_id != actor.id or task.proposal_by_user_id == actor.id:
            raise DomainError("Свою задачу оценивать нельзя.")
        self.repo.version(task, version)
        if task.status != "BACKLOG" or task.rated_points is not None:
            raise DomainError("Эта задача уже оценена или отменена.")
        task.rated_points, task.rated_by_user_id, task.rated_at = points, actor.id, utcnow()
        task.rating_source = "MANUAL"
        task.first_accepted_at = task.first_accepted_at or utcnow()
        task.status = "TODO"
        task.version += 1
        self.repo.audit(actor, task, "rate", {"rated_points": None}, {"rated_points": points})
        self.repo.add(
            Notification(
                recipient_user_id=task.proposal_by_user_id,
                kind="rated",
                payload={
                    "task_id": task.id,
                    "title": task.title,
                    "points": points,
                    "evaluator": actor.display_name,
                    "actor": actor.display_name,
                    "status": task.status,
                    "shared": task.kind == "SHARED",
                },
                dedupe_key=f"rated:{task.id}:{task.version}",
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
        await self.validate_participants(task)
        if task.deleted_at or not self.can_manage(task, actor):
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
            for user_id in task.participant_user_ids:
                self.repo.add(
                    ScoreEvent(
                        user_id=user_id,
                        category="task",
                        points=-task.awarded_points,
                        effective_at=task.completed_at,
                        operation_key=f"task:{task.id}:reopen:{task.version}:{user_id}",
                    )
                )
            task.completed_at = None
            task.awarded_points = None
        if status == "DONE":
            if task.rated_points is None:
                raise DomainError("Сначала второй пользователь должен оценить задачу.")
            task.awarded_points, task.completed_at = task.rated_points, utcnow()
            for user_id in task.participant_user_ids:
                self.repo.add(
                    ScoreEvent(
                        user_id=user_id,
                        category="task",
                        points=task.awarded_points,
                        effective_at=task.completed_at,
                        operation_key=f"task:{task.id}:done:{task.version}:{user_id}",
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
        await self.validate_participants(task)
        if (
            task.deleted_at
            or not self.can_manage(task, actor)
            or task.status in ("DONE", "CANCELLED")
        ):
            raise DomainError("Редактировать можно только свою активную задачу.")
        self.repo.version(task, version)
        if not changes:
            raise DomainError("Укажи хотя бы одно изменение.")
        # Resolve the next approver before touching tracked attributes: a user lookup
        # can autoflush, while proposal and evaluator must change atomically.
        next_evaluator_id = (
            (await self.user_repo.peer(actor)).id
            if {"title", "estimated_minutes"} & changes.keys()
            else None
        )
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
        semantic = old["title"] != task.title or old["estimated_minutes"] != task.estimated_minutes
        if semantic:
            task.proposed_points = task.rated_points or task.proposed_points
            task.proposal_by_user_id = actor.id
            assert next_evaluator_id is not None
            task.evaluator_user_id = next_evaluator_id
            task.rated_points = task.rated_by_user_id = task.rated_at = None
            task.rating_source = None
            task.started_at = None
            task.status = "BACKLOG"
            await self.repo.enqueue_rating(
                task.evaluator_user_id, self.settings.rating_notification_debounce_seconds
            )
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

    @atomic
    async def reject(
        self, actor: User, task_id: int, version: int, confirmed: bool = False
    ) -> Task:
        actor = await self.actor(actor)
        task = await self.repo.get(Task, task_id, lock=True)
        await self.validate_participants(task)
        self.repo.version(task, version)
        if task.deleted_at or task.status != "BACKLOG" or task.evaluator_user_id != actor.id:
            raise DomainError("Отклонение недоступно.")
        if confirmed is not True:
            raise DomainError("Подтверди отклонение задачи.")
        task.status, task.cancelled_at = "CANCELLED", utcnow()
        task.version += 1
        self.repo.audit(actor, task, "reject", {"status": "BACKLOG"}, {"status": "CANCELLED"})
        self.repo.add(
            Notification(
                recipient_user_id=task.proposal_by_user_id,
                kind="task_rejected",
                payload={
                    "task_id": task.id,
                    "title": task.title,
                    "actor": actor.display_name,
                    "status": task.status,
                    "shared": task.kind == "SHARED",
                },
                dedupe_key=f"task_rejected:{task.id}:{task.version}",
                available_at=utcnow(),
            )
        )
        await self.repo.flush()
        return task

    @atomic
    async def rerate(
        self, actor: User, task_id: int, points: int, version: int, confirmed: bool = False
    ) -> Task:
        actor = await self.actor(actor)
        points = rating_points(points)
        task = await self.repo.get(Task, task_id, lock=True)
        await self.validate_participants(task)
        self.repo.version(task, version)
        if (
            task.deleted_at
            or task.status not in ("TODO", "IN_PROGRESS")
            or task.rating_source != "MANUAL"
            or task.rated_by_user_id != actor.id
        ):
            raise DomainError("Переоценка недоступна.")
        if points == task.rated_points:
            return task
        if confirmed is not True:
            raise DomainError("Подтверди старую и новую оценку.")
        old = task.rated_points
        task.rated_points, task.rated_at = points, utcnow()
        task.version += 1
        self.repo.audit(actor, task, "rerate", {"rated_points": old}, {"rated_points": points})
        recipient = (
            (await self.user_repo.peer(actor)).id if task.kind == "SHARED" else task.owner_user_id
        )
        self.repo.add(
            Notification(
                recipient_user_id=recipient,
                kind="rated",
                payload={
                    "task_id": task.id,
                    "title": task.title,
                    "points": points,
                    "evaluator": actor.display_name,
                    "actor": actor.display_name,
                    "old_points": old,
                    "status": task.status,
                    "shared": task.kind == "SHARED",
                },
                dedupe_key=f"rated:{task.id}:{task.version}",
                available_at=utcnow(),
            )
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
            "status": task.status,
            "proposed_points": task.proposed_points,
            "proposal_by_user_id": task.proposal_by_user_id,
            "evaluator_user_id": task.evaluator_user_id,
            "rated_points": task.rated_points,
            "rating_source": task.rating_source,
        }
