from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.models.entities import (
    ActivityBoard,
    ActivityTracker,
    Tag,
    Task,
    TaskTag,
    TemplateTag,
    TrackerTag,
)
from app.repositories.base import Repository
from app.repositories.tasks import TaskRepository


class ActivityRepository(Repository):
    async def tags(self) -> list[Tag]:
        return list(await self.session.scalars(select(Tag).order_by(Tag.name, Tag.id)))

    async def boards(self, owner_id: int) -> list[ActivityBoard]:
        return list(
            await self.session.scalars(
                select(ActivityBoard)
                .where(ActivityBoard.owner_user_id == owner_id)
                .order_by(ActivityBoard.id)
            )
        )

    async def trackers(self, board_id: int) -> list[ActivityTracker]:
        return list(
            await self.session.scalars(
                select(ActivityTracker)
                .where(ActivityTracker.board_id == board_id)
                .order_by(ActivityTracker.position, ActivityTracker.id)
            )
        )

    async def tracker_tags(self, tracker_ids: list[int]) -> dict[int, list[int]]:
        rows = await self.session.execute(
            select(TrackerTag.tracker_id, TrackerTag.tag_id)
            .where(TrackerTag.tracker_id.in_(tracker_ids))
            .order_by(TrackerTag.tag_id)
        )
        result: dict[int, list[int]] = {}
        for tracker_id, tag_id in rows:
            result.setdefault(tracker_id, []).append(tag_id)
        return result

    async def task_tags(self, task_ids: list[int]) -> dict[int, list[Tag]]:
        rows = await self.session.execute(
            select(TaskTag.task_id, Tag)
            .join(Tag, Tag.id == TaskTag.tag_id)
            .where(TaskTag.task_id.in_(task_ids))
            .order_by(Tag.name)
        )
        result: dict[int, list[Tag]] = {}
        for task_id, tag in rows:
            result.setdefault(task_id, []).append(tag)
        return result

    async def template_tags(self, template_ids: list[int]) -> dict[int, list[Tag]]:
        rows = await self.session.execute(
            select(TemplateTag.template_id, Tag)
            .join(Tag, Tag.id == TemplateTag.tag_id)
            .where(TemplateTag.template_id.in_(template_ids))
            .order_by(Tag.name)
        )
        result: dict[int, list[Tag]] = {}
        for template_id, tag in rows:
            result.setdefault(template_id, []).append(tag)
        return result

    async def completed(self, user_id: int, start: datetime, end: datetime) -> list[Task]:
        rows = await self.session.scalars(
            select(Task)
            .where(
                Task.status == "DONE",
                Task.deleted_at.is_(None),
                Task.completed_at >= start.astimezone(UTC),
                Task.completed_at < end.astimezone(UTC),
                or_(Task.owner_user_id == user_id, Task.shared_user_id == user_id),
                TaskRepository.visible(),
            )
            .order_by(Task.completed_at, Task.id)
        )
        return list(rows)
