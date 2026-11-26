"""Activity measures use completed-task snapshots and local calendar dates."""

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.config.settings import Settings
from app.models.entities import (
    ActivityBoard,
    ActivityTracker,
    Tag,
    Task,
    TaskTag,
    TaskTemplate,
    TemplateTag,
    TrackerTag,
    User,
)
from app.repositories.activity import ActivityRepository
from app.repositories.tasks import TaskRepository
from app.services.base import AuthorizedService
from app.services.rules import DomainError, operation_key, text_field
from app.services.tasks import TaskService
from app.services.transactions import atomic
from app.utils.time import utcnow


def metric_value(metric: str, unit: str | None, snapshot: dict[str, Any] | None) -> int:
    if metric == "TASKS":
        return 1
    if not snapshot:
        return 0
    expected = "PER_TIME" if metric == "TIME" else "PER_QUANTITY"
    if snapshot.get("pricing_mode") != expected:
        return 0
    if metric == "QUANTITY" and snapshot.get("unit_label") != unit:
        return 0
    quantity = snapshot.get("quantity")
    return quantity if type(quantity) is int and quantity > 0 else 0


def activity_summary(days: dict[date, int], year: int, today: date) -> tuple[int, int, int]:
    active = sorted(
        day for day, value in days.items() if day.year == year and value > 0 and day <= today
    )
    best = run = 0
    previous = None
    for day in active:
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    current = run if previous and previous in (today, today - timedelta(days=1)) else 0
    return len(active), current, best


def tag_json(tag: Tag) -> dict[str, Any]:
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "archived": tag.archived,
        "version": tag.version,
    }


def board_json(board: ActivityBoard) -> dict[str, Any]:
    return {"id": board.id, "name": board.name, "version": board.version}


def tracker_json(tracker: ActivityTracker, tags: list[int]) -> dict[str, Any]:
    return {
        "id": tracker.id,
        "board_id": tracker.board_id,
        "name": tracker.name,
        "color": tracker.color,
        "metric": tracker.metric,
        "unit": tracker.unit,
        "position": tracker.position,
        "tag_ids": tags,
        "version": tracker.version,
    }


class ActivityService(AuthorizedService):
    repo: ActivityRepository

    def __init__(self, repo: ActivityRepository, settings: Settings) -> None:
        super().__init__(repo, settings)
        self.repo = repo

    async def board(self, actor: User, board_id: int, lock: bool = False) -> ActivityBoard:
        await self.actor(actor)
        board = await self.repo.get(ActivityBoard, board_id, lock=lock)
        if board.owner_user_id != actor.id:
            raise DomainError("Доска недоступна.")
        return board

    async def tracker(self, actor: User, tracker_id: int, lock: bool = False) -> ActivityTracker:
        tracker = await self.repo.get(ActivityTracker, tracker_id, lock=lock)
        await self.board(actor, tracker.board_id)
        return tracker

    async def valid_tags(self, ids: list[int], existing: set[int] | None = None) -> list[int]:
        if len(ids) > 50 or len(set(ids)) != len(ids):
            raise DomainError("Недопустимый список тегов.")
        found = list(
            await self.repo.session.scalars(
                select(Tag)
                .where(Tag.id.in_(ids))
                .order_by(Tag.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if len(found) != len(ids) or any(
            tag.archived and tag.id not in (existing or set()) for tag in found
        ):
            raise DomainError("Тег недоступен или архивирован.")
        return ids

    @staticmethod
    def color(value: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise DomainError("Цвет должен быть в формате #RRGGBB.")
        return value.lower()

    @atomic
    async def save_tag(
        self,
        actor: User,
        name: str,
        color: str,
        tag_id: int | None = None,
        version: int | None = None,
        key: str | None = None,
    ) -> Tag:
        await self.actor(actor)
        name = text_field(name, 80, "Название")
        normalized = name.casefold()
        if len(normalized) > 80:
            raise DomainError("Название тега слишком длинное после нормализации.")
        await self.repo.operation_lock("tag-catalog")
        if tag_id is None:
            if key is None:
                raise DomainError("Укажи ключ операции.")
            key = operation_key(key)
            existing = await self.repo.session.scalar(select(Tag).where(Tag.operation_key == key))
            if existing:
                return existing
        duplicate = await self.repo.session.scalar(
            select(Tag).where(Tag.normalized_name == normalized)
        )
        if duplicate and duplicate.id != tag_id:
            raise DomainError("Тег с таким названием уже существует.")
        if tag_id is None:
            tag = Tag(
                name=name, normalized_name=normalized, color=self.color(color), operation_key=key
            )
            self.repo.add(tag)
        else:
            tag = await self.repo.get(Tag, tag_id, lock=True)
            if version is None:
                raise DomainError("Укажи версию тега.")
            self.repo.version(tag, version)
            tag.name, tag.normalized_name, tag.color = name, normalized, self.color(color)
            tag.version += 1
        await self.repo.flush()
        return tag

    @atomic
    async def archive_tag(self, actor: User, tag_id: int, version: int) -> Tag:
        await self.actor(actor)
        tag = await self.repo.get(Tag, tag_id, lock=True)
        self.repo.version(tag, version)
        tag.archived = True
        tag.version += 1
        return tag

    @atomic
    async def set_tags(
        self, actor: User, entity_id: int, version: int, ids: list[int], template: bool = False
    ) -> None:
        await self.actor(actor)
        if template:
            item = await self.repo.get(TaskTemplate, entity_id, lock=True)
            if (
                item.creator_user_id not in await self.configured_participant_ids()
                or item.is_deleted
            ):
                raise DomainError("Шаблон недоступен.")
            self.repo.version(item, version)
            previous = set(
                await self.repo.session.scalars(
                    select(TemplateTag.tag_id).where(TemplateTag.template_id == entity_id)
                )
            )
            await self.valid_tags(ids, previous)
            await self.repo.session.execute(
                delete(TemplateTag).where(TemplateTag.template_id == entity_id)
            )
            for tag_id in ids:
                self.repo.add(TemplateTag(template_id=entity_id, tag_id=tag_id))
            item.version += 1
            self.repo.audit(actor, item, "tags", {}, {"tag_ids": ids})
        else:
            task = await self.repo.get(Task, entity_id, lock=True)
            task_service = TaskService(TaskRepository(self.repo.session), self.settings)
            await task_service.validate_participants(task)
            if (
                task.deleted_at
                or task.status == "CANCELLED"
                or not task_service.can_manage(task, actor)
            ):
                raise DomainError("Задача недоступна для изменения тегов.")
            self.repo.version(task, version)
            previous = set(
                await self.repo.session.scalars(
                    select(TaskTag.tag_id).where(TaskTag.task_id == entity_id)
                )
            )
            await self.valid_tags(ids, previous)
            await self.repo.session.execute(delete(TaskTag).where(TaskTag.task_id == entity_id))
            for tag_id in ids:
                self.repo.add(TaskTag(task_id=entity_id, tag_id=tag_id))
            task.version += 1
            self.repo.audit(actor, task, "tags", {}, {"tag_ids": ids})
        await self.repo.flush()

    async def aggregate(
        self, actor: User, board_id: int, user_id: int, year: int
    ) -> dict[str, Any]:
        board = await self.board(actor, board_id)
        if user_id not in await self.configured_participant_ids() or not 1 <= year <= 9998:
            raise DomainError("Недопустимый участник или год.")
        zone = ZoneInfo(self.settings.default_timezone)
        today = utcnow().astimezone(zone).date()
        start = datetime(year, 1, 1, tzinfo=zone)
        end = datetime(year + 1, 1, 1, tzinfo=zone)
        trackers = await self.repo.trackers(board.id)
        tracker_tags = await self.repo.tracker_tags([t.id for t in trackers])
        tasks = await self.repo.completed(user_id, start, end)
        tags = await self.repo.task_tags([task.id for task in tasks])
        output = []
        for tracker in trackers:
            days: dict[date, int] = {}
            wanted = set(tracker_tags.get(tracker.id, []))
            for task in tasks:
                if not wanted.intersection(tag.id for tag in tags.get(task.id, [])):
                    continue
                value = metric_value(tracker.metric, tracker.unit, task.template_snapshot)
                if value and task.completed_at:
                    day = task.completed_at.astimezone(zone).date()
                    days[day] = days.get(day, 0) + value
            active, current, best = activity_summary(days, year, today)
            output.append(
                {
                    **tracker_json(tracker, tracker_tags.get(tracker.id, [])),
                    "days": {key.isoformat(): value for key, value in days.items()},
                    "active_days": active,
                    "current_streak": current,
                    "best_streak": best,
                }
            )
        return {
            "board": board_json(board),
            "trackers": output,
            "today": today.isoformat(),
            "timezone": self.settings.default_timezone,
        }

    @atomic
    async def create_board(self, actor: User, name: str, key: str) -> ActivityBoard:
        await self.actor(actor)
        key = operation_key(key)
        await self.repo.operation_lock(key)
        existing = await self.repo.session.scalar(
            select(ActivityBoard).where(ActivityBoard.operation_key == key)
        )
        if existing:
            if existing.owner_user_id != actor.id:
                raise DomainError("Доска недоступна.")
            return existing
        board = ActivityBoard(
            owner_user_id=actor.id, name=text_field(name, 80, "Название"), operation_key=key
        )
        self.repo.add(board)
        await self.repo.flush()
        self.repo.audit(actor, board, "create", {}, {"name": board.name})
        return board

    @atomic
    async def edit_board(
        self, actor: User, board_id: int, version: int, name: str
    ) -> ActivityBoard:
        board = await self.board(actor, board_id, lock=True)
        self.repo.version(board, version)
        old = board.name
        board.name = text_field(name, 80, "Название")
        board.version += 1
        self.repo.audit(actor, board, "edit", {"name": old}, {"name": board.name})
        return board

    @atomic
    async def delete_board(self, actor: User, board_id: int, version: int) -> None:
        board = await self.board(actor, board_id, lock=True)
        self.repo.version(board, version)
        self.repo.audit(actor, board, "delete", {"name": board.name}, {})
        await self.repo.session.delete(board)

    @atomic
    async def save_tracker(
        self, actor: User, board_id: int, data: dict[str, Any], tracker_id: int | None = None
    ) -> ActivityTracker:
        board = await self.board(actor, board_id, lock=True)
        existing_tags = (
            set((await self.repo.tracker_tags([tracker_id])).get(tracker_id, []))
            if tracker_id is not None
            else set()
        )
        ids = await self.valid_tags(data["tag_ids"], existing_tags)
        if not ids:
            raise DomainError("Выбери хотя бы один тег.")
        metric = data["metric"]
        unit = data.get("unit")
        if metric not in ("TASKS", "TIME", "QUANTITY"):
            raise DomainError("Неизвестный показатель.")
        if metric == "QUANTITY":
            unit = text_field(unit, 40, "Единица") if isinstance(unit, str) else ""
            if not unit:
                raise DomainError("Укажи единицу измерения.")
        elif unit is not None:
            raise DomainError("Единица допустима только для количества.")
        if tracker_id is None:
            if not isinstance(data.get("operation_key"), str):
                raise DomainError("Укажи ключ операции.")
            key = operation_key(data["operation_key"])
            await self.repo.operation_lock(key)
            existing = await self.repo.session.scalar(
                select(ActivityTracker).where(ActivityTracker.operation_key == key)
            )
            if existing:
                if existing.board_id != board.id:
                    raise DomainError("Трекер недоступен.")
                return existing
            tracker = ActivityTracker(
                board_id=board.id,
                operation_key=key,
                name="",
                color="#000000",
                metric=metric,
                unit=unit,
                position=0,
            )
            self.repo.add(tracker)
            await self.repo.flush()
        else:
            tracker = await self.tracker(actor, tracker_id, lock=True)
            self.repo.version(tracker, data["version"])
            tracker.version += 1
            await self.repo.session.execute(
                delete(TrackerTag).where(TrackerTag.tracker_id == tracker.id)
            )
        tracker.name = text_field(data["name"], 80, "Название")
        tracker.color = self.color(data["color"])
        tracker.metric, tracker.unit, tracker.position = metric, unit, data["position"]
        for tag_id in ids:
            self.repo.add(TrackerTag(tracker_id=tracker.id, tag_id=tag_id))
        self.repo.audit(actor, tracker, "save", {}, {"tag_ids": ids})
        await self.repo.flush()
        return tracker

    @atomic
    async def delete_tracker(self, actor: User, tracker_id: int, version: int) -> None:
        tracker = await self.tracker(actor, tracker_id, lock=True)
        self.repo.version(tracker, version)
        self.repo.audit(actor, tracker, "delete", {"name": tracker.name}, {})
        await self.repo.session.delete(tracker)

    async def day(
        self, actor: User, tracker_id: int, day: date, user_id: int, page: int
    ) -> dict[str, Any]:
        tracker = await self.tracker(actor, tracker_id)
        if user_id not in await self.configured_participant_ids() or not 0 <= page <= 100000:
            raise DomainError("Недопустимый участник или страница.")
        zone = ZoneInfo(self.settings.default_timezone)
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        tasks = await self.repo.completed(user_id, start, end)
        task_tags = await self.repo.task_tags([task.id for task in tasks])
        wanted = set((await self.repo.tracker_tags([tracker.id])).get(tracker.id, []))
        matched = [
            task
            for task in tasks
            if wanted.intersection(tag.id for tag in task_tags.get(task.id, []))
            and metric_value(tracker.metric, tracker.unit, task.template_snapshot)
        ]
        total = sum(
            metric_value(tracker.metric, tracker.unit, task.template_snapshot) for task in matched
        )
        page = min(page, max(0, (len(matched) - 1) // 20))
        items = [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "owner_user_id": task.owner_user_id,
                "shared_user_id": task.shared_user_id,
                "awarded_points": task.awarded_points,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "template_snapshot": task.template_snapshot,
                "tags": [tag_json(tag) for tag in task_tags.get(task.id, [])],
            }
            for task in matched[page * 20 : page * 20 + 20]
        ]
        return {
            "items": items,
            "tracker": tracker_json(tracker, sorted(wanted)),
            "count": len(matched),
            "page": page,
            "page_size": 20,
            "total": total,
            "points": sum(task.awarded_points or 0 for task in matched),
        }
