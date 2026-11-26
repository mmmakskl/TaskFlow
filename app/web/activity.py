"""Authenticated Mini App activity endpoints."""

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.entities import User
from app.repositories.activity import ActivityRepository
from app.services.activity import ActivityService, board_json, tag_json, tracker_json


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TagInput(Input):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=7, max_length=7)
    version: int | None = Field(default=None, ge=1)
    operation_key: str | None = Field(default=None, min_length=1, max_length=80)


class Version(Input):
    version: int = Field(ge=1)


class TagAssignment(Version):
    tag_ids: list[int] = Field(max_length=50)


class BoardCreate(Input):
    name: str = Field(min_length=1, max_length=80)
    operation_key: str = Field(min_length=1, max_length=80)


class BoardEdit(Version):
    name: str = Field(min_length=1, max_length=80)


class TrackerInput(Input):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=7, max_length=7)
    metric: Literal["TASKS", "TIME", "QUANTITY"]
    unit: str | None = Field(default=None, max_length=40)
    position: int = Field(default=0, ge=0, le=10000)
    tag_ids: list[int] = Field(min_length=1, max_length=50)
    operation_key: str | None = Field(default=None, min_length=1, max_length=80)
    version: int | None = Field(default=None, ge=1)


def register_activity_routes(
    router: APIRouter,
    settings: Settings,
    actor_dependency: Callable[..., Any],
    session_dependency: Callable[..., Any],
) -> None:
    actor_dep = Depends(actor_dependency)
    session_dep = Depends(session_dependency)

    def service(session: AsyncSession) -> ActivityService:
        return ActivityService(ActivityRepository(session), settings)

    @router.get("/api/miniapp/tags")
    async def tags(
        actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> list[dict[str, Any]]:
        await service(session).actor(actor)
        return [tag_json(tag) for tag in await service(session).repo.tags()]

    @router.post("/api/miniapp/tags", status_code=201)
    async def create_tag(
        body: TagInput, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, Any]:
        return tag_json(
            await service(session).save_tag(actor, body.name, body.color, key=body.operation_key)
        )

    @router.patch("/api/miniapp/tags/{tag_id}")
    async def edit_tag(
        tag_id: int, body: TagInput, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, Any]:
        return tag_json(
            await service(session).save_tag(actor, body.name, body.color, tag_id, body.version)
        )

    @router.post("/api/miniapp/tags/{tag_id}/archive")
    async def archive_tag(
        tag_id: int, body: Version, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, Any]:
        return tag_json(await service(session).archive_tag(actor, tag_id, body.version))

    @router.put("/api/miniapp/tasks/{task_id}/tags")
    async def task_tags(
        task_id: int,
        body: TagAssignment,
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, bool]:
        await service(session).set_tags(actor, task_id, body.version, body.tag_ids)
        return {"ok": True}

    @router.put("/api/miniapp/templates/{template_id}/tags")
    async def template_tags(
        template_id: int,
        body: TagAssignment,
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, bool]:
        await service(session).set_tags(
            actor, template_id, body.version, body.tag_ids, template=True
        )
        return {"ok": True}

    @router.get("/api/miniapp/boards")
    async def boards(
        actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> list[dict[str, Any]]:
        svc = service(session)
        await svc.actor(actor)
        return [board_json(board) for board in await svc.repo.boards(actor.id)]

    @router.post("/api/miniapp/boards", status_code=201)
    async def create_board(
        body: BoardCreate, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, Any]:
        return board_json(await service(session).create_board(actor, body.name, body.operation_key))

    @router.patch("/api/miniapp/boards/{board_id}")
    async def edit_board(
        board_id: int, body: BoardEdit, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, Any]:
        return board_json(
            await service(session).edit_board(actor, board_id, body.version, body.name)
        )

    @router.delete("/api/miniapp/boards/{board_id}")
    async def delete_board(
        board_id: int, body: Version, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, bool]:
        await service(session).delete_board(actor, board_id, body.version)
        return {"ok": True}

    @router.post("/api/miniapp/boards/{board_id}/trackers", status_code=201)
    async def create_tracker(
        board_id: int,
        body: TrackerInput,
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, Any]:
        tracker = await service(session).save_tracker(actor, board_id, body.model_dump())
        return tracker_json(tracker, body.tag_ids)

    @router.patch("/api/miniapp/trackers/{tracker_id}")
    async def edit_tracker(
        tracker_id: int,
        body: TrackerInput,
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, Any]:
        svc = service(session)
        tracker = await svc.tracker(actor, tracker_id)
        tracker = await svc.save_tracker(actor, tracker.board_id, body.model_dump(), tracker_id)
        return tracker_json(tracker, body.tag_ids)

    @router.delete("/api/miniapp/trackers/{tracker_id}")
    async def delete_tracker(
        tracker_id: int, body: Version, actor: User = actor_dep, session: AsyncSession = session_dep
    ) -> dict[str, bool]:
        await service(session).delete_tracker(actor, tracker_id, body.version)
        return {"ok": True}

    @router.get("/api/miniapp/boards/{board_id}/activity")
    async def activity(
        board_id: int,
        year: int = Query(ge=1, le=9998),
        user_id: int = Query(ge=1),
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, Any]:
        return await service(session).aggregate(actor, board_id, user_id, year)

    @router.get("/api/miniapp/trackers/{tracker_id}/days/{day}")
    async def activity_day(
        tracker_id: int,
        day: date,
        user_id: int = Query(ge=1),
        page: int = Query(default=0, ge=0, le=100000),
        actor: User = actor_dep,
        session: AsyncSession = session_dep,
    ) -> dict[str, Any]:
        return await service(session).day(actor, tracker_id, day, user_id, page)
