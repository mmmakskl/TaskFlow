"""Repository substitutes for isolated business tests; never emulate SQL or locks."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models import Notification, Penalty, PenaltyType, ScoreEvent, User
from app.repositories.base import Repository
from app.repositories.penalties import PenaltyRepository
from app.repositories.tasks import TaskRepository
from app.repositories.templates import TaskTemplateRepository
from app.repositories.users import UserRepository
from app.services.rules import DomainError, StaleAction, authenticated_identity

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


class MemorySession:
    def __init__(self):
        self.rows = []
        self.pending = []
        self.flush_error = None
        self.savepoints = 0
        self.rollbacks = 0

    def add(self, entity):
        if entity not in self.rows and entity not in self.pending:
            self.pending.append(entity)

    async def flush(self):
        if self.flush_error:
            raise self.flush_error
        for entity in self.pending:
            for column in entity.__table__.columns:
                if getattr(entity, column.name) is None:
                    if column.primary_key:
                        setattr(
                            entity,
                            column.name,
                            max((r.id for r in self.rows if type(r) is type(entity)), default=0)
                            + 1,
                        )
                    elif column.name in ("created_at", "updated_at"):
                        setattr(entity, column.name, NOW)
                    elif column.default is not None:
                        default = column.default.arg
                        setattr(
                            entity, column.name, default(None) if callable(default) else default
                        )
            self.rows.append(entity)
        self.pending = []

    def of(self, model):
        return [row for row in self.rows if isinstance(row, model)]

    @asynccontextmanager
    async def begin_nested(self):
        await self.flush()
        old_rows = list(self.rows)
        snapshots = [
            (
                row,
                deepcopy(
                    {
                        key: value
                        for key, value in row.__dict__.items()
                        if key != "_sa_instance_state"
                    }
                ),
            )
            for row in old_rows
        ]
        self.savepoints += 1
        try:
            yield
            await self.flush()
        except Exception:
            self.rows = old_rows
            self.pending = []
            for row, snapshot in snapshots:
                for key in list(row.__dict__):
                    if key != "_sa_instance_state":
                        del row.__dict__[key]
                row.__dict__.update(snapshot)
            self.rollbacks += 1
            raise


class MemoryRepository(Repository):
    async def get(self, model, entity_id, lock=False):
        if type(entity_id) is not int or entity_id < 1:
            raise StaleAction()
        entity = next((row for row in self.session.of(model) if row.id == entity_id), None)
        if entity is None:
            raise StaleAction()
        return entity

    async def users(self):
        return self.session.of(User)

    async def operation_lock(self, key):
        pass  # Concurrency and actual locks are covered by PostgreSQL integration tests.


class MemoryTasks(MemoryRepository, TaskRepository):
    async def by_operation(self, key):
        from app.models import Task

        return next((row for row in self.session.of(Task) if row.operation_key == key), None)

    async def enqueue_rating(self, evaluator_id, debounce_seconds):
        if not any(
            row.recipient_user_id == evaluator_id
            and row.kind == "rating_pending"
            and row.status == "pending"
            for row in self.session.of(Notification)
        ):
            self.add(
                Notification(
                    recipient_user_id=evaluator_id,
                    kind="rating_pending",
                    dedupe_key=f"pending:{evaluator_id}",
                    payload={},
                    available_at=NOW,
                )
            )


class MemoryPenalties(MemoryRepository, PenaltyRepository):
    async def by_operation(self, key):
        return next((row for row in self.session.of(Penalty) if row.operation_key == key), None)

    async def types(self, include_inactive=False, page=0):
        self.page(page)
        rows = sorted(
            [row for row in self.session.of(PenaltyType) if include_inactive or row.is_active],
            key=lambda row: (row.sort_order, row.id),
        )[page * 5 : page * 5 + 6]
        return rows[:5], len(rows) > 5

    async def list(self, page=0):
        self.page(page)
        rows = sorted(
            [row for row in self.session.of(Penalty) if not row.deleted_at], key=lambda row: -row.id
        )[page * 5 : page * 5 + 6]
        return rows[:5], len(rows) > 5


class MemoryUsers(UserRepository):
    async def authenticate(self, actor):
        stored = next(
            (user for user in self.session.of(User) if user.telegram_id == actor.telegram_id), None
        )
        if stored is None:
            raise DomainError("Доступ запрещён.")
        authenticated_identity(
            actor.id, actor.telegram_id, stored.id, stored.telegram_id, self.settings.authorized_ids
        )
        return stored

    async def participants(self):
        users = [
            user
            for user in self.session.of(User)
            if user.telegram_id in self.settings.authorized_ids
        ]
        if len(users) != 2:
            raise DomainError("Не удалось определить двух участников.")
        return users


class MemoryResults(MemoryRepository):
    async def totals(self, start, end):
        output = {}
        for event in self.session.of(ScoreEvent):
            if start <= event.effective_at < end:
                result = output.setdefault(event.user_id, {})
                result[event.category] = result.get(event.category, 0) + event.points
        return output

    async def earliest(self):
        return min((event.effective_at for event in self.session.of(ScoreEvent)), default=None)

    async def weekly_totals(self, start, end, timezone):
        output = {}
        for event in self.session.of(ScoreEvent):
            if start <= event.effective_at < end:
                date = event.effective_at.astimezone(ZoneInfo(timezone)).date()
                from datetime import timedelta

                key = (
                    (date - timedelta(days=date.weekday())).isoformat(),
                    event.user_id,
                    event.category,
                )
                output[key] = output.get(key, 0) + event.points
        return output


class MemoryTemplates(MemoryRepository, TaskTemplateRepository):
    async def by_operation(self, key):
        from app.models import TaskTemplate

        return next(
            (row for row in self.session.of(TaskTemplate) if row.operation_key == key), None
        )

    async def list(self, active_only=True, page=0, *, creator_ids=None):
        from app.models import TaskTemplate

        return [
            row
            for row in self.session.of(TaskTemplate)
            if (not active_only or row.is_active)
            and (creator_ids is None or row.creator_user_id in creator_ids)
        ][page * 5 : page * 5 + 5]

    async def count(self, active_only=True, *, creator_ids=None):
        from app.models import TaskTemplate

        return len(
            [
                row
                for row in self.session.of(TaskTemplate)
                if (not active_only or row.is_active)
                and (creator_ids is None or row.creator_user_id in creator_ids)
            ]
        )
