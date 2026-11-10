import logging
from datetime import datetime

from app.config.settings import Settings
from app.models import Notification, ScoreEvent, Task, TaskLink, TaskTarget, TaskTemplate, User
from app.repositories.tasks import TaskRepository
from app.services.base import AuthorizedService
from app.services.deadlines import deadline_delay
from app.services.rules import (
    DomainError,
    StaleAction,
    estimated_minutes,
    operation_key,
    rating_points,
    template_points,
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
        template_quantity: int | None = None,
        parent_task_id: int | None = None,
        parent_version: int | None = None,
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
        parent = None
        if parent_task_id is not None:
            await self.repo.operation_lock("task-graph")
            parent = await self.repo.get(Task, parent_task_id, lock=True)
            await self.validate_participants(parent)
            if (
                parent.deleted_at
                or parent.status in ("DONE", "CANCELLED")
                or parent.parent_task_id is not None
                or not self.can_manage(parent, actor)
            ):
                raise DomainError("Нельзя добавить подзадачу к этой задаче.")
            if parent_version is None:
                raise StaleAction()
            self.repo.version(parent, parent_version)
        elif parent_version is not None:
            raise DomainError("Не указана родительская задача.")
        try:
            target = TaskTarget(target)
        except (ValueError, TypeError) as exc:
            raise DomainError("Недопустимый исполнитель.") from exc
        child_participants = (
            (peer.id,)
            if target == TaskTarget.PEER
            else (actor.id, peer.id)
            if target == TaskTarget.SHARED
            else (actor.id,)
        )
        if parent and child_participants != parent.participant_user_ids:
            raise DomainError("Подзадача должна использовать состав участников эпика.")
        proposal = rating_points(proposed_points) if proposed_points is not None else None
        template = None
        calculated_points = proposal
        if template_id is not None:
            template = await self.repo.get(TaskTemplate, template_id, lock=True)
            if template.creator_user_id not in allowed:
                raise DomainError("Этот шаблон недоступен.")
            if type(template_version) is not int:
                raise StaleAction()
            self.repo.version(template, template_version)
            if not template.is_active:
                raise StaleAction()
            calculated_points = template_points(
                template.pricing_mode,
                template.points,
                template.base_quantity,
                template_quantity,
            )
            if (
                template.pricing_mode != "FIXED"
                and proposal is not None
                and proposal != calculated_points
            ):
                raise DomainError("Цена шаблона изменилась. Пересчитай задачу и попробуй снова.")
            if template.pricing_mode != "FIXED" or proposal is None:
                proposal = calculated_points
        elif template_version is not None or template_quantity is not None:
            raise DomainError("Не указан шаблон.")
        auto = bool(
            template
            and title == template.title
            and estimate == template.estimated_minutes
            and proposal == calculated_points
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
                    "points": calculated_points,
                    "base_points": template.points,
                    "pricing_mode": template.pricing_mode,
                    "base_quantity": template.base_quantity,
                    "unit_label": template.unit_label,
                    "quantity": template_quantity,
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
            parent_task_id=parent.id if parent else None,
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
        if parent:
            old_version = parent.version
            parent.version += 1
            self.repo.audit(
                actor,
                parent,
                "subtask_add",
                {"version": old_version},
                {"child_task_id": task.id, "version": parent.version},
            )
            await self.repo.flush()
        logger.info("task_created entity_id=%s actor_id=%s", task.id, actor.id)
        return task

    async def _bump_parent(
        self, actor: User, task: Task, action: str, changes: dict[str, object]
    ) -> None:
        if task.parent_task_id is None:
            return
        parent = await self.repo.get(Task, task.parent_task_id, lock=True)
        old_version = parent.version
        parent.version += 1
        self.repo.audit(
            actor,
            parent,
            action,
            {"child_task_id": task.id, "version": old_version},
            {"child_task_id": task.id, "version": parent.version, **changes},
        )

    @staticmethod
    def _dependency_path(edges: list[tuple[int, int]], start: int, target: int) -> bool:
        graph: dict[int, list[int]] = {}
        for blocker_id, dependent_id in edges:
            graph.setdefault(blocker_id, []).append(dependent_id)
        pending = [start]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        return False

    @atomic
    async def attach_subtask(
        self,
        actor: User,
        parent_id: int,
        child_id: int,
        parent_version: int,
        child_version: int,
        key: str,
    ) -> Task:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(f"task-graph-op:{key}")
        previous = await self.repo.graph_operation(key)
        if previous:
            if (
                previous.actor_user_id != actor.id
                or previous.new_data.get("action") != "attach_subtask"
            ):
                raise DomainError("Идентификатор операции уже использован.")
            return await self.repo.get(Task, int(previous.new_data["child_task_id"]))
        await self.repo.operation_lock("task-graph")
        if parent_id == child_id:
            raise DomainError("Задача не может быть своей подзадачей.")
        rows = {
            entity_id: await self.repo.get(Task, entity_id, lock=True)
            for entity_id in sorted((parent_id, child_id))
        }
        parent, child = rows[parent_id], rows[child_id]
        await self.validate_participants(parent)
        await self.validate_participants(child)
        if (
            parent.deleted_at
            or parent.status in ("DONE", "CANCELLED")
            or parent.parent_task_id is not None
            or not self.can_manage(parent, actor)
        ):
            raise DomainError("Нельзя изменять состав этой задачи.")
        if child.deleted_at or child.parent_task_id is not None or child.status == "CANCELLED":
            raise DomainError("Эту задачу нельзя прикрепить к эпику.")
        if await self.repo.children(child.id):
            raise DomainError("Вложенные эпики пока не поддерживаются.")
        if child.participant_user_ids != parent.participant_user_ids:
            raise DomainError("У подзадачи должен быть тот же исполнитель, что у эпика.")
        self.repo.version(parent, parent_version)
        self.repo.version(child, child_version)
        parent_before, child_before = parent.version, child.version
        child.parent_task_id = parent.id
        child.version += 1
        parent.version += 1
        self.repo.audit(
            actor,
            child,
            "attach_parent",
            {"parent_task_id": None, "version": child_before},
            {"parent_task_id": parent.id, "version": child.version},
        )
        self.repo.audit(
            actor,
            parent,
            "subtask_add",
            {"version": parent_before},
            {
                "action": "attach_subtask",
                "child_task_id": child.id,
                "operation_key": key,
                "version": parent.version,
            },
        )
        await self.repo.flush()
        return child

    @atomic
    async def detach_subtask(
        self,
        actor: User,
        parent_id: int,
        child_id: int,
        parent_version: int,
        child_version: int,
        key: str,
    ) -> Task:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(f"task-graph-op:{key}")
        previous = await self.repo.graph_operation(key)
        if previous:
            if (
                previous.actor_user_id != actor.id
                or previous.new_data.get("action") != "detach_subtask"
            ):
                raise DomainError("Идентификатор операции уже использован.")
            return await self.repo.get(Task, int(previous.new_data["child_task_id"]))
        await self.repo.operation_lock("task-graph")
        rows = {
            entity_id: await self.repo.get(Task, entity_id, lock=True)
            for entity_id in sorted((parent_id, child_id))
        }
        parent, child = rows[parent_id], rows[child_id]
        await self.validate_participants(parent)
        await self.validate_participants(child)
        if (
            parent.deleted_at
            or parent.status in ("DONE", "CANCELLED")
            or not self.can_manage(parent, actor)
            or child.parent_task_id != parent.id
        ):
            raise DomainError("Открепить эту задачу нельзя.")
        self.repo.version(parent, parent_version)
        self.repo.version(child, child_version)
        parent_before, child_before = parent.version, child.version
        child.parent_task_id = None
        child.version += 1
        parent.version += 1
        self.repo.audit(
            actor,
            child,
            "detach_parent",
            {"parent_task_id": parent.id, "version": child_before},
            {"parent_task_id": None, "version": child.version},
        )
        self.repo.audit(
            actor,
            parent,
            "subtask_remove",
            {"version": parent_before},
            {
                "action": "detach_subtask",
                "child_task_id": child.id,
                "operation_key": key,
                "version": parent.version,
            },
        )
        await self.repo.flush()
        return child

    @atomic
    async def create_link(
        self,
        actor: User,
        task_id: int,
        target_id: int,
        kind: str,
        task_version: int,
        target_version: int,
        key: str,
    ) -> TaskLink:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(f"task-graph-op:{key}")
        existing_by_key = await self.repo.link_by_operation(key)
        if existing_by_key:
            if existing_by_key.created_by_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing_by_key
        await self.repo.operation_lock("task-graph")
        if kind not in ("RELATED", "DEPENDS_ON") or task_id == target_id:
            raise DomainError("Недопустимая связь между задачами.")
        rows = {
            entity_id: await self.repo.get(Task, entity_id, lock=True)
            for entity_id in sorted((task_id, target_id))
        }
        current, target = rows[task_id], rows[target_id]
        await self.validate_participants(current)
        await self.validate_participants(target)
        if (
            current.deleted_at
            or target.deleted_at
            or current.status == "CANCELLED"
            or target.status == "CANCELLED"
        ):
            raise DomainError("Связать можно только доступные задачи.")
        self.repo.version(current, task_version)
        self.repo.version(target, target_version)
        if kind == "RELATED":
            if not self.can_manage(current, actor) and not self.can_manage(target, actor):
                raise DomainError("Связь может изменить управляющий задачей.")
            a_id, b_id = sorted((task_id, target_id))
        else:
            if not self.can_manage(current, actor):
                raise DomainError("Связь может изменить управляющий зависимой задачей.")
            if current.parent_task_id == target.id or target.parent_task_id == current.id:
                raise DomainError("Связь с эпиком уже задана структурой подзадач.")
            a_id, b_id = target.id, current.id  # blocker -> dependent
            if self._dependency_path(await self.repo.active_dependency_edges(), b_id, a_id):
                raise DomainError("Эта зависимость создаст цикл.")
        if any(
            link.kind == kind and (link.task_a_id, link.task_b_id) == (a_id, b_id)
            for link in await self.repo.links_for(task_id)
        ):
            raise DomainError("Такая связь уже есть.")
        link = TaskLink(
            task_a_id=a_id,
            task_b_id=b_id,
            kind=kind,
            created_by_user_id=actor.id,
            operation_key=key,
        )
        self.repo.add(link)
        await self.repo.flush()
        old_current, old_target = current.version, target.version
        current.version += 1
        target.version += 1
        self.repo.audit(
            actor,
            current,
            "link_add",
            {"version": old_current},
            {"link_id": link.id, "operation_key": key, "version": current.version},
        )
        self.repo.audit(
            actor,
            target,
            "link_add",
            {"version": old_target},
            {"link_id": link.id, "version": target.version},
        )
        await self.repo.flush()
        return link

    @atomic
    async def remove_link(self, actor: User, link_id: int, version: int, key: str) -> TaskLink:
        actor = await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(f"task-graph-op:{key}")
        previous = await self.repo.graph_operation(key)
        if previous:
            if (
                previous.actor_user_id != actor.id
                or previous.new_data.get("action") != "remove_link"
            ):
                raise DomainError("Идентификатор операции уже использован.")
            return await self.repo.get(TaskLink, int(previous.new_data["link_id"]))
        await self.repo.operation_lock("task-graph")
        link = await self.repo.get(TaskLink, link_id, lock=True)
        self.repo.version(link, version)
        if link.removed_at:
            raise StaleAction()
        rows = {
            entity_id: await self.repo.get(Task, entity_id, lock=True)
            for entity_id in sorted((link.task_a_id, link.task_b_id))
        }
        a, b = rows[link.task_a_id], rows[link.task_b_id]
        await self.validate_participants(a)
        await self.validate_participants(b)
        managed = (
            self.can_manage(a, actor) or self.can_manage(b, actor)
            if link.kind == "RELATED"
            else self.can_manage(b, actor)
        )
        if not managed:
            raise DomainError("Удалить эту связь может только управляющий задачей.")
        key = operation_key(key)
        old = {"removed_at": None, "version": link.version}
        link.removed_at, link.removed_by_operation_key = utcnow(), key
        link.version += 1
        old_a, old_b = a.version, b.version
        a.version += 1
        b.version += 1
        self.repo.audit(
            actor,
            a,
            "link_remove",
            {"version": old_a},
            {
                "action": "remove_link",
                "link_id": link.id,
                "operation_key": key,
                "version": a.version,
            },
        )
        self.repo.audit(
            actor,
            b,
            "link_remove",
            {"version": old_b},
            {"link_id": link.id, "version": b.version},
        )
        self.repo.audit(actor, link, "remove", old, {"removed_at": str(link.removed_at)})
        await self.repo.flush()
        return link

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
        await self.repo.operation_lock("task-graph")
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
        await self._bump_parent(actor, task, "subtask_rate", {"rated_points": points})
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
        await self.repo.operation_lock("task-graph")
        task = await self.repo.get(Task, task_id, lock=True)
        await self.validate_participants(task)
        if task.deleted_at or not self.can_manage(task, actor):
            raise DomainError("Статус меняет только владелец задачи.")
        self.repo.version(task, version)
        parent = None
        if task.parent_task_id is not None:
            parent = await self.repo.get(Task, task.parent_task_id, lock=True)
            await self.validate_participants(parent)
        if task.status == "DONE" and status in ("TODO", "IN_PROGRESS") and parent:
            if parent.status == "DONE":
                raise DomainError("Сначала открой родительскую задачу.")
        children = await self.repo.children(task.id, lock=True)
        if status == "DONE" and children and any(child.status != "DONE" for child in children):
            raise DomainError("Сначала заверши все подзадачи.")
        if status == "DONE":
            blockers = await self.repo.blockers(task.id)
            pending_blockers = [blocker for blocker in blockers if blocker.status != "DONE"]
            if pending_blockers:
                raise DomainError("Сначала заверши блокирующую задачу.")
        if status == "CANCELLED" and children:
            raise DomainError("Сначала открепи подзадачи от родительской задачи.")
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
        if parent:
            old_parent_version = parent.version
            parent.version += 1
            self.repo.audit(
                actor,
                parent,
                "subtask_status",
                {
                    "child_task_id": task.id,
                    "child_status": old["status"],
                    "version": old_parent_version,
                },
                {
                    "child_task_id": task.id,
                    "child_status": status,
                    "version": parent.version,
                },
            )
        await self.repo.flush()
        logger.info("task_status entity_id=%s status=%s", task.id, status)
        return task

    @atomic
    async def edit(self, actor: User, task_id: int, version: int, **changes: object) -> Task:
        actor = await self.actor(actor)
        await self.repo.operation_lock("task-graph")
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
        await self._bump_parent(
            actor, task, "subtask_edit", {"title": task.title, "status": task.status}
        )
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
        await self.repo.operation_lock("task-graph")
        task = await self.repo.get(Task, task_id, lock=True)
        await self.validate_participants(task)
        self.repo.version(task, version)
        if task.deleted_at or task.status != "BACKLOG" or task.evaluator_user_id != actor.id:
            raise DomainError("Отклонение недоступно.")
        if confirmed is not True:
            raise DomainError("Подтверди отклонение задачи.")
        task.status, task.cancelled_at = "CANCELLED", utcnow()
        task.version += 1
        await self._bump_parent(actor, task, "subtask_reject", {"status": task.status})
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
        await self.repo.operation_lock("task-graph")
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
        await self._bump_parent(actor, task, "subtask_rerate", {"rated_points": points})
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
