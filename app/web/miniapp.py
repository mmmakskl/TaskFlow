"""Authenticated JSON API and static entry point for the Telegram Mini App."""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import date, datetime
from datetime import time as day_time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from aiogram import Bot
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.models import Penalty, PenaltyType, Task, TaskTarget, TaskTemplate, User
from app.repositories.activity import ActivityRepository
from app.repositories.base import Repository
from app.repositories.results import ResultRepository
from app.repositories.tasks import TaskRepository
from app.repositories.templates import TaskTemplateRepository
from app.services.activity import ActivityService, tag_json
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.rules import DomainError
from app.services.settings import SettingsService
from app.services.tasks import TaskService
from app.services.templates import TaskTemplateService
from app.utils.time import utcnow

logger = logging.getLogger(__name__)
STATIC_INDEX = Path(__file__).parent / "static" / "index.html"
STATIC_ASSETS = ("miniapp.css", "activity.js", "miniapp.js")


def miniapp_html() -> str:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    for name in STATIC_ASSETS:
        digest = hashlib.sha256((STATIC_INDEX.parent / name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={digest}"')
    return html


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    target: TaskTarget = TaskTarget.SELF
    proposed_points: int | None = Field(default=None, ge=1, le=10000)
    deadline: date | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=10080)
    comment: str | None = Field(default=None, max_length=1500)
    operation_key: str = Field(min_length=1, max_length=80)
    template_id: int | None = Field(default=None, ge=1)
    template_version: int | None = Field(default=None, ge=1)
    template_quantity: int | None = Field(default=None, ge=1, le=10080)
    tag_ids: list[int] | None = Field(default=None, max_length=50)
    parent_task_id: int | None = Field(default=None, ge=1)
    parent_version: int | None = Field(default=None, ge=1)


class TaskEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_ids: list[int] | None = Field(default=None, max_length=50)

    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    deadline: date | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=10080)
    comment: str | None = Field(default=None, max_length=1500)


class TaskAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    status: str | None = None
    points: int | None = Field(default=None, ge=1, le=10000)
    confirmed: bool = False


class SubtaskRelationEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    child_version: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=80)


class TaskLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_task_id: int = Field(ge=1)
    target_version: int = Field(ge=1)
    version: int = Field(ge=1)
    kind: Literal["RELATED", "DEPENDS_ON"]
    operation_key: str = Field(min_length=1, max_length=80)


class TaskLinkRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    operation_key: str = Field(min_length=1, max_length=80)


class TemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_ids: list[int] | None = Field(default=None, max_length=50)

    title: str = Field(min_length=1, max_length=200)
    points: int = Field(ge=1, le=10000)
    estimated_minutes: int | None = Field(default=None, ge=1, le=10080)
    pricing_mode: Literal["FIXED", "PER_TIME", "PER_QUANTITY"] = "FIXED"
    base_quantity: int | None = Field(default=None, ge=1, le=10080)
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    operation_key: str = Field(min_length=1, max_length=80)


class TemplateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_ids: list[int] | None = Field(default=None, max_length=50)

    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    points: int | None = Field(default=None, ge=1, le=10000)
    estimated_minutes: int | None = Field(default=None, ge=1, le=10080)
    pricing_mode: Literal["FIXED", "PER_TIME", "PER_QUANTITY"] | None = None
    base_quantity: int | None = Field(default=None, ge=1, le=10080)
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None


class PenaltyPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: int = Field(ge=1)
    quantity: int | None = Field(default=None, ge=1, le=10000)
    manual: int | None = Field(default=None, ge=1, le=1000000000)


class PenaltyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: int = Field(ge=1)
    preview: dict[str, Any]
    operation_key: str = Field(min_length=1, max_length=80)
    comment: str | None = Field(default=None, max_length=1500)
    confirmed: bool


class PenaltyTypeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    emoji: str = Field(min_length=1, max_length=16)
    description: str = Field(default="", max_length=500)
    calculation_type: str
    rate: int = Field(ge=0, le=1000000)


class PenaltyTypeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    emoji: str | None = Field(default=None, min_length=1, max_length=16)
    description: str | None = Field(default=None, max_length=500)
    calculation_type: str | None = None
    rate: int | None = Field(default=None, ge=0, le=1000000)
    is_active: bool | None = None


class SettingsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekly_target: int | None = Field(default=None, ge=1, le=1000000)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


def validate_init_data(raw: str, bot_token: str, *, now: int | None = None) -> dict[str, Any]:
    """Validate Telegram's signed initData and return the untrusted fields only after checking."""
    if not raw or len(raw) > 16384:
        raise ValueError("Invalid Telegram authorization data")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate Telegram authorization field")
        values[key] = value
    received_hash = values.pop("hash", None)
    if not received_hash or len(received_hash) != 64:
        raise ValueError("Missing Telegram signature")
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid Telegram signature")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        telegram_id = user["id"]
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Missing Telegram user or auth date") from None
    current = int(time.time()) if now is None else now
    if type(auth_date) is not int or auth_date > current + 60 or current - auth_date > 86400:
        raise ValueError("Expired Telegram authorization")
    if type(telegram_id) is not int or telegram_id <= 0:
        raise ValueError("Invalid Telegram user")
    return {"telegram_id": telegram_id, "user": user, "auth_date": auth_date}


def task_json(task: Task, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": task.id,
        "version": task.version,
        "title": task.title,
        "comment": task.comment,
        "target": "SHARED"
        if task.kind == "SHARED"
        else ("PEER" if task.owner_user_id != task.creator_user_id else "SELF"),
        "kind": task.kind,
        "status": task.status,
        "owner": names.get(task.owner_user_id, "Участник"),
        "creator": names.get(task.creator_user_id, "Участник"),
        "proposal_by": names.get(task.proposal_by_user_id, "Участник"),
        "evaluator": names.get(task.evaluator_user_id, "Участник"),
        "participants": [names.get(uid, "Участник") for uid in task.participant_user_ids],
        "proposed_points": task.proposed_points,
        "points": task.awarded_points if task.status == "DONE" else task.rated_points,
        "awarded_points": task.awarded_points,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "estimated_minutes": task.estimated_minutes,
        "template_snapshot": task.template_snapshot,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "rating_source": task.rating_source,
        "rated_by": names.get(task.rated_by_user_id) if task.rated_by_user_id else None,
        "parent_task_id": task.parent_task_id,
    }


def task_points(task: Task) -> int | None:
    if task.status == "DONE":
        return task.awarded_points
    if task.status in ("TODO", "IN_PROGRESS"):
        return task.rated_points
    return None


def template_json(template: TaskTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "version": template.version,
        "title": template.title,
        "points": template.points,
        "estimated_minutes": template.estimated_minutes,
        "pricing_mode": template.pricing_mode,
        "base_quantity": template.base_quantity,
        "unit_label": template.unit_label,
        "is_active": template.is_active,
        "is_deleted": template.is_deleted,
    }


def penalty_type_json(kind: PenaltyType) -> dict[str, Any]:
    return {
        "id": kind.id,
        "version": kind.version,
        "name": kind.name,
        "emoji": kind.emoji,
        "description": kind.description,
        "calculation_type": kind.calculation_type,
        "rate": kind.rate,
        "is_active": kind.is_active,
    }


def penalty_json(penalty: Penalty, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": penalty.id,
        "version": penalty.version,
        "target": names.get(penalty.target_user_id, "Участник"),
        "name": penalty.name_snapshot,
        "points": penalty.points,
        "comment": penalty.comment,
        "created_at": penalty.created_at.isoformat(),
        "deleted": penalty.deleted_at is not None,
    }


def create_router(settings: Settings, factory: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            async with session.begin():
                yield session

    async def actor_dependency(
        authorization: str | None = Header(default=None),
        session: AsyncSession = Depends(session_dependency),
    ) -> User:
        scheme, _, raw = (authorization or "").partition(" ")
        if scheme.lower() != "tma" or not raw:
            raise HTTPException(401, "Open TaskFlow from Telegram")
        try:
            identity = validate_init_data(raw, settings.bot_token.get_secret_value())
        except ValueError:
            raise HTTPException(401, "Telegram authorization expired; reopen the app") from None
        if identity["telegram_id"] not in settings.authorized_ids:
            raise HTTPException(403, "Access denied")
        try:
            return await Repository(session).user(identity["telegram_id"])
        except DomainError:
            raise HTTPException(403, "Access denied") from None

    def services(session: AsyncSession) -> tuple[TaskService, PenaltyService, ResultService]:
        base = Repository(session)
        return (
            TaskService(TaskRepository(session), settings),
            PenaltyService(base, settings),
            ResultService(ResultRepository(session), settings),
        )

    from app.web.activity import register_activity_routes

    register_activity_routes(router, settings, actor_dependency, session_dependency)

    @router.get("/miniapp")
    async def miniapp_entry() -> HTMLResponse:
        return HTMLResponse(
            miniapp_html(),
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @router.get("/api/miniapp/bootstrap")
    async def bootstrap(
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        tasks, _, results = services(session)
        peer = await tasks.user_repo.peer(actor)
        label, scores = await results.week()
        _, pending_count = await tasks.repo.pending_next(actor)
        active_tasks, active_count = await tasks.repo.list(actor, "active", scope="mine")
        return {
            "user": {
                "id": actor.id,
                "name": actor.display_name,
                "timezone": actor.timezone,
                "weekly_target": actor.weekly_target,
            },
            "peer": {
                "id": peer.id,
                "name": peer.display_name,
                "timezone": peer.timezone,
                "weekly_target": peer.weekly_target,
            },
            "week": {
                "label": label,
                "scores": [
                    {
                        "user_id": user.id,
                        "name": user.display_name,
                        "earned": earned,
                        "lost": lost,
                        "net": earned - lost,
                        "target": user.weekly_target,
                    }
                    for user, earned, lost in scores
                ],
            },
            "pending_count": pending_count,
            "active_count": active_count,
            "active_preview": [
                task_json(t, {actor.id: actor.display_name, peer.id: peer.display_name})
                for t in active_tasks[:3]
            ],
            "server_time": utcnow().isoformat(),
        }

    @router.get("/api/miniapp/tasks")
    async def task_list(
        scope: str = Query(default="mine"),
        tag_id: int | None = Query(default=None, ge=1),
        search: str | None = Query(default=None, max_length=200),
        filter_name: str = Query(default="all", alias="filter"),
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        rows, count = await task_service.repo.list(
            actor, filter_name, page, scope=scope, tag_id=tag_id, search=search
        )
        participants = await task_service.user_repo.participants()
        names = {user.id: user.display_name for user in participants}
        summary = (
            await task_service.repo.summary(actor, scope=scope)
            if scope in ("mine", "peer")
            else None
        )
        items: list[dict[str, Any]] = []
        tags_by_task = await ActivityRepository(session).task_tags([task.id for task in rows])
        for task in rows:
            item = task_json(task, names)
            item["tags"] = [tag_json(tag) for tag in tags_by_task.get(task.id, [])]
            children = await task_service.repo.children(task.id)
            item["children"] = [task_json(child, names) for child in children]
            item["subtask_summary"] = {
                "count": len(children),
                "done_count": sum(1 for child in children if child.status == "DONE"),
                "agreed_points": sum(task_points(child) or 0 for child in children),
                "progress": round(
                    100 * sum(1 for child in children if child.status == "DONE") / len(children)
                )
                if children
                else 0,
            }
            items.append(item)
        return {
            "items": items,
            "count": count,
            "page": min(page, max(0, (count - 1) // 5)),
            "page_size": 5,
            "summary": (
                {
                    "todo_points": summary.todo_points,
                    "in_progress_points": summary.in_progress_points,
                    "planned_points": summary.planned_points,
                    "pending_count": summary.pending_count,
                    "completed_points": summary.completed_points,
                }
                if summary
                else None
            ),
        }

    @router.get("/api/miniapp/tasks/{parent_id}/attachable")
    async def attachable_tasks(
        parent_id: int,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        parent = await task_service.view(actor, parent_id)
        if not task_service.can_manage(parent, actor) or parent.parent_task_id is not None:
            raise HTTPException(403, "Недостаточно прав")
        candidates: dict[int, Task] = {}
        for scope in ("mine", "peer", "shared", "assigned"):
            for filter_name in ("all", "done"):
                page = 0
                while True:
                    rows, count = await task_service.repo.list(
                        actor, filter_name, page, scope=scope
                    )
                    candidates.update(
                        {
                            task.id: task
                            for task in rows
                            if task.id != parent.id and task.parent_task_id is None
                        }
                    )
                    if (page + 1) * 5 >= count:
                        break
                    page += 1
        eligible = []
        for task in candidates.values():
            if (
                task.participant_user_ids == parent.participant_user_ids
                and task.status != "CANCELLED"
                and not await task_service.repo.children(task.id)
            ):
                eligible.append(task)
        names = {u.id: u.display_name for u in await task_service.repo.users()}
        return {"items": [task_json(task, names) for task in eligible]}

    @router.get("/api/miniapp/tasks/search")
    async def task_search(
        q: str = Query(min_length=1, max_length=100),
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        rows, count = await task_service.repo.search(actor, q, page)
        names = {user.id: user.display_name for user in await task_service.repo.users()}
        return {
            "items": [task_json(task, names) for task in rows],
            "count": count,
            "page": min(page, max(0, (count - 1) // 5)),
            "page_size": 5,
        }

    @router.get("/api/miniapp/tasks/{task_id}")
    async def task_detail(
        task_id: int,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        task = await task_service.view(actor, task_id)
        names = {user.id: user.display_name for user in await task_service.repo.users()}
        result = task_json(task, names)
        tags_by_task = await ActivityRepository(session).task_tags([task.id])
        result["tags"] = [tag_json(tag) for tag in tags_by_task.get(task.id, [])]
        result["comment"] = task.comment
        result["can_manage"] = task_service.can_manage(task, actor)
        result["child_target"] = (
            "SHARED"
            if task.kind == "SHARED"
            else "SELF"
            if task.owner_user_id == actor.id
            else "PEER"
        )
        parent_task = (
            await task_service.view(actor, task.parent_task_id)
            if task.parent_task_id is not None
            else None
        )
        result["parent"] = (
            {"id": parent_task.id, "title": parent_task.title, "status": parent_task.status}
            if parent_task
            else None
        )
        result["blocked_by_parent"] = bool(parent_task and parent_task.status == "DONE")
        result["can_rerate"] = task.rated_by_user_id == actor.id and task.status in (
            "TODO",
            "IN_PROGRESS",
        )
        result["is_evaluator"] = task.evaluator_user_id == actor.id and task.status == "BACKLOG"
        children = await task_service.repo.children(task.id)
        result["children"] = [task_json(child, names) for child in children]
        result["subtask_summary"] = {
            "count": len(children),
            "done_count": sum(1 for child in children if child.status == "DONE"),
            "agreed_points": sum(task_points(child) or 0 for child in children),
            "progress": round(
                100 * sum(1 for child in children if child.status == "DONE") / len(children)
            )
            if children
            else 0,
        }
        result["bonus_points"] = task_points(task)
        result["blockers"] = [
            {"id": blocker.id, "title": blocker.title, "status": blocker.status}
            for blocker in await task_service.repo.blockers(task.id)
            if blocker.status != "DONE"
        ]
        links = await task_service.repo.links_for(task.id)
        result["links"] = []
        for link in links:
            if link.removed_at:
                continue
            other_id = link.task_b_id if link.task_a_id == task.id else link.task_a_id
            other = await task_service.view(actor, other_id)
            result["links"].append(
                {
                    "id": link.id,
                    "version": link.version,
                    "kind": link.kind,
                    "task_id": other.id,
                    "title": other.title,
                    "status": other.status,
                }
            )
        return result

    @router.post("/api/miniapp/tasks", status_code=201)
    async def task_create(
        body: TaskInput,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        deadline = (
            datetime.combine(body.deadline, day_time.max, ZoneInfo(actor.timezone))
            if body.deadline
            else None
        )
        task = await task_service.create(
            actor,
            body.title,
            body.operation_key,
            body.comment,
            deadline,
            body.estimated_minutes,
            target=body.target,
            proposed_points=body.proposed_points,
            template_id=body.template_id,
            template_version=body.template_version,
            template_quantity=body.template_quantity,
            parent_task_id=body.parent_task_id,
            parent_version=body.parent_version,
            tag_ids=body.tag_ids,
        )
        names = {user.id: user.display_name for user in await task_service.repo.users()}
        return task_json(task, names)

    @router.post("/api/miniapp/tasks/{parent_id}/subtasks/{child_id}")
    async def subtask_attach(
        parent_id: int,
        child_id: int,
        body: SubtaskRelationEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        child = await task_service.attach_subtask(
            actor, parent_id, child_id, body.version, body.child_version, body.operation_key
        )
        return task_json(child, {u.id: u.display_name for u in await task_service.repo.users()})

    @router.delete("/api/miniapp/tasks/{parent_id}/subtasks/{child_id}")
    async def subtask_detach(
        parent_id: int,
        child_id: int,
        body: SubtaskRelationEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        child = await task_service.detach_subtask(
            actor, parent_id, child_id, body.version, body.child_version, body.operation_key
        )
        return task_json(child, {u.id: u.display_name for u in await task_service.repo.users()})

    @router.post("/api/miniapp/tasks/{task_id}/links", status_code=201)
    async def task_link_create(
        task_id: int,
        body: TaskLinkInput,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        link = await task_service.create_link(
            actor,
            task_id,
            body.target_task_id,
            body.kind,
            body.version,
            body.target_version,
            body.operation_key,
        )
        return {"id": link.id, "version": link.version, "kind": link.kind}

    @router.delete("/api/miniapp/task-links/{link_id}")
    async def task_link_remove(
        link_id: int,
        body: TaskLinkRemove,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        link = await task_service.remove_link(actor, link_id, body.version, body.operation_key)
        return {"id": link.id, "removed": True}

    @router.patch("/api/miniapp/tasks/{task_id}")
    async def task_edit(
        task_id: int,
        body: TaskEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        changes = body.model_dump(exclude={"version", "tag_ids"}, exclude_unset=True)
        if "deadline" in changes:
            value = changes["deadline"]
            changes["deadline"] = (
                datetime.combine(value, day_time.max, ZoneInfo(actor.timezone)) if value else None
            )
        if not changes and body.tag_ids is None:
            raise HTTPException(422, "No task fields to update")
        task = (
            await task_service.edit(actor, task_id, body.version, **changes)
            if changes
            else await task_service.view(actor, task_id)
        )
        if body.tag_ids is not None:
            await ActivityService(ActivityRepository(session), settings).set_tags(
                actor, task_id, task.version if changes else body.version, body.tag_ids
            )

        names = {user.id: user.display_name for user in await task_service.repo.users()}
        return task_json(task, names)

    @router.post("/api/miniapp/tasks/{task_id}/actions")
    async def task_action(
        task_id: int,
        body: TaskAction,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        if body.status in ("TODO", "IN_PROGRESS", "DONE", "CANCELLED"):
            task = await task_service.change_status(
                actor, task_id, body.status, body.version, body.confirmed
            )
        elif body.status == "REJECT":
            task = await task_service.reject(actor, task_id, body.version, body.confirmed)
        elif body.status == "RATE":
            if body.points is None:
                raise HTTPException(422, "A rating is required")
            task = await task_service.rate(actor, task_id, body.points, body.version)
        elif body.status == "RERATE":
            if body.points is None:
                raise HTTPException(422, "A rating is required")
            task = await task_service.rerate(
                actor, task_id, body.points, body.version, body.confirmed
            )
        else:
            raise HTTPException(422, "Unsupported task action")
        names = {user.id: user.display_name for user in await task_service.repo.users()}
        return task_json(task, names)

    @router.get("/api/miniapp/inbox")
    async def inbox(
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        task_service, _, _ = services(session)
        rows, count = await task_service.repo.list(actor, "backlog", page, pending=True)
        names = {user.id: user.display_name for user in await task_service.repo.users()}
        return {"items": [task_json(task, names) for task in rows], "count": count, "page": page}

    @router.get("/api/miniapp/results")
    async def results(
        offset: int = Query(default=0, ge=-5200, le=0),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, _, result_service = services(session)
        label, scores = await result_service.week(offset)
        return {
            "label": label,
            "scores": [
                {
                    "user_id": user.id,
                    "name": user.display_name,
                    "earned": earned,
                    "lost": lost,
                    "net": earned - lost,
                    "target": user.weekly_target,
                }
                for user, earned, lost in scores
            ],
        }

    @router.get("/api/miniapp/results/trend")
    async def result_trend(
        weeks: int = Query(default=5),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, _, result_service = services(session)
        rows = await result_service.trend(weeks)
        return {
            "weeks": [
                {
                    "start_date": start_date,
                    "label": label,
                    "scores": [
                        {
                            "user_id": user.id,
                            "name": user.display_name,
                            "earned": earned,
                            "lost": lost,
                            "net": earned - lost,
                            "target": user.weekly_target,
                        }
                        for user, earned, lost in scores
                    ],
                }
                for start_date, label, scores in rows
            ]
        }

    @router.get("/api/miniapp/results/history")
    async def result_history(
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, _, result_service = services(session)
        weeks, resolved_page, has_next = await result_service.history(page)
        return {
            "items": [
                {
                    "label": label,
                    "scores": [
                        {
                            "user_id": user.id,
                            "name": user.display_name,
                            "earned": earned,
                            "lost": lost,
                            "net": earned - lost,
                            "target": user.weekly_target,
                        }
                        for user, earned, lost in scores
                    ],
                }
                for label, scores in weeks
            ],
            "page": resolved_page,
            "has_next": has_next,
        }

    @router.get("/api/miniapp/templates")
    async def template_list(
        include_inactive: bool = False,
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        rows = await service.list(actor, not include_inactive, page)
        tags_by_template = await ActivityRepository(session).template_tags([t.id for t in rows])
        return {
            "items": [
                {
                    **template_json(t),
                    "tags": [tag_json(tag) for tag in tags_by_template.get(t.id, [])],
                }
                for t in rows
            ],
            "count": await service.count(actor, not include_inactive),
            "page": page,
        }

    @router.post("/api/miniapp/templates", status_code=201)
    async def template_create(
        body: TemplateInput,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        return template_json(
            await service.create(
                actor,
                body.title,
                body.points,
                body.operation_key,
                body.estimated_minutes,
                pricing_mode=body.pricing_mode,
                base_quantity=body.base_quantity,
                unit_label=body.unit_label,
                tag_ids=body.tag_ids,
            )
        )

    @router.get("/api/miniapp/templates/{template_id}")
    async def template_detail(
        template_id: int,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        template = await service.view(actor, template_id)
        tags = (await ActivityRepository(session).template_tags([template.id])).get(template.id, [])
        return {**template_json(template), "tags": [tag_json(tag) for tag in tags]}

    @router.patch("/api/miniapp/templates/{template_id}")
    async def template_edit(
        template_id: int,
        body: TemplateEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        changes = body.model_dump(exclude={"version", "tag_ids"}, exclude_unset=True)
        if not changes and body.tag_ids is None:
            raise HTTPException(422, "No template fields to update")
        template = (
            await service.edit(actor, template_id, body.version, **changes)
            if changes
            else await service.view(actor, template_id)
        )
        if body.tag_ids is not None:
            await ActivityService(ActivityRepository(session), settings).set_tags(
                actor,
                template_id,
                template.version if changes else body.version,
                body.tag_ids,
                template=True,
            )
        return template_json(template)

    @router.delete("/api/miniapp/templates/{template_id}")
    async def template_delete(
        template_id: int,
        version: int = Query(ge=1),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, bool]:
        service = TaskTemplateService(TaskTemplateRepository(session), settings)
        await service.delete(actor, template_id, version)
        return {"ok": True}

    @router.get("/api/miniapp/penalties")
    async def penalty_list(
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        rows, has_next = await service.list(page)
        names = {user.id: user.display_name for user in await service.users()}
        return {
            "items": [penalty_json(p, names) for p in rows],
            "page": page,
            "has_next": has_next,
            "types": [penalty_type_json(t) for t in (await service.types())[0]],
            "participants": [{"id": u.id, "name": u.display_name} for u in await service.users()],
        }

    @router.post("/api/miniapp/penalties/preview")
    async def penalty_preview(
        body: PenaltyPreviewInput,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        return await service.preview(body.type_id, body.quantity, body.manual)

    @router.post("/api/miniapp/penalties", status_code=201)
    async def penalty_create(
        body: PenaltyCreate,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        penalty = await service.create(
            actor, body.target_id, body.preview, body.operation_key, body.confirmed, body.comment
        )
        names = {user.id: user.display_name for user in await service.users()}
        return penalty_json(penalty, names)

    @router.delete("/api/miniapp/penalties/{penalty_id}")
    async def penalty_delete(
        penalty_id: int,
        version: int = Query(ge=1),
        confirmed: bool = Query(default=False),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, bool]:
        _, service, _ = services(session)
        await service.delete(actor, penalty_id, version, confirmed)
        return {"ok": True}

    @router.get("/api/miniapp/penalty-types")
    async def penalty_types(
        include_inactive: bool = False,
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        rows, has_next = await service.types(include_inactive, page)
        return {"items": [penalty_type_json(t) for t in rows], "page": page, "has_next": has_next}

    @router.post("/api/miniapp/penalty-types", status_code=201)
    async def penalty_type_create(
        body: PenaltyTypeInput,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        kind = await service.configure(actor, None, **body.model_dump())
        return penalty_type_json(kind)

    @router.patch("/api/miniapp/penalty-types/{type_id}")
    async def penalty_type_edit(
        type_id: int,
        body: PenaltyTypeEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        _, service, _ = services(session)
        changes = body.model_dump(exclude={"version"}, exclude_unset=True)
        if not changes:
            raise HTTPException(422, "No penalty type fields to update")
        return penalty_type_json(await service.configure(actor, type_id, body.version, **changes))

    @router.get("/api/miniapp/settings")
    async def get_settings(actor: User = Depends(actor_dependency)) -> dict[str, Any]:
        return {
            "weekly_target": actor.weekly_target,
            "timezone": actor.timezone,
            "default_timezone": settings.default_timezone,
        }

    @router.patch("/api/miniapp/settings")
    async def edit_settings(
        body: SettingsEdit,
        actor: User = Depends(actor_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> dict[str, Any]:
        service = SettingsService(Repository(session), settings)
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(422, "No settings to update")
        for field, value in values.items():
            await service.change(actor, field, str(value))
        return {
            "weekly_target": actor.weekly_target,
            "timezone": actor.timezone,
            "default_timezone": settings.default_timezone,
        }

    return router


async def configure_miniapp_menu(bot: Bot, settings: Settings) -> None:
    """Compatibility entry point: reset legacy app menus to authenticated chat commands."""
    from app.bot.navigation import register_commands

    await register_commands(bot, settings.authorized_ids)
