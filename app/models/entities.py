from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.utils.time import utcnow


class TaskStatus(StrEnum):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskTarget(StrEnum):
    SELF = "SELF"
    PEER = "PEER"
    SHARED = "SHARED"


class CalculationType(StrEnum):
    PER_MINUTE = "PER_MINUTE"
    FIXED = "FIXED"
    MANUAL = "MANUAL"


class Base(DeclarativeBase):
    metadata = MetaData(schema="taskflow")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), unique=True)
    timezone: Mapped[str] = mapped_column(String(64))
    weekly_target: Mapped[int] = mapped_column(Integer, default=10000)
    last_rating_notification_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("weekly_target > 0"),
        CheckConstraint("telegram_id > 0", name="ck_users_telegram_positive"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 50", name="ck_users_name"),
    )


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    evaluator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    creator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20), default="PERSONAL")
    shared_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    proposed_points: Mapped[int | None] = mapped_column(Integer)
    proposal_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    first_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating_source: Mapped[str | None] = mapped_column(String(20))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("task_templates.id"))
    template_version: Mapped[int | None] = mapped_column(Integer)
    template_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    title: Mapped[str] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(1500))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.BACKLOG)
    rated_points: Mapped[int | None] = mapped_column(Integer)
    rated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    awarded_points: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_key: Mapped[str] = mapped_column(String(80), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("kind IN ('PERSONAL','SHARED')", name="ck_tasks_kind"),
        CheckConstraint(
            "(kind = 'PERSONAL' AND shared_user_id IS NULL) OR (kind = 'SHARED' AND shared_user_id IS NOT NULL AND shared_user_id <> owner_user_id)",
            name="ck_tasks_participants",
        ),
        CheckConstraint("evaluator_user_id <> proposal_by_user_id", name="ck_tasks_other_approver"),
        CheckConstraint(
            "proposed_points IS NULL OR proposed_points BETWEEN 1 AND 10000",
            name="ck_tasks_proposed_points",
        ),
        CheckConstraint("rated_points IS NULL OR rated_points BETWEEN 1 AND 10000"),
        CheckConstraint("awarded_points IS NULL OR awarded_points BETWEEN 1 AND 10000"),
        CheckConstraint("length(title) BETWEEN 1 AND 200"),
        CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080",
            name="ck_tasks_estimated_minutes",
        ),
        CheckConstraint("status IN ('BACKLOG','TODO','IN_PROGRESS','DONE','CANCELLED')"),
        CheckConstraint("rated_by_user_id IS NULL OR rated_by_user_id = evaluator_user_id"),
        CheckConstraint(
            "status <> 'DONE' OR (completed_at IS NOT NULL AND awarded_points IS NOT NULL)"
        ),
        CheckConstraint(
            "((template_id IS NULL AND template_version IS NULL AND template_snapshot IS NULL) OR (template_id IS NOT NULL AND template_version IS NOT NULL AND template_version > 0 AND template_snapshot IS NOT NULL AND jsonb_typeof(template_snapshot) = 'object' AND jsonb_typeof(template_snapshot->'title') = 'string' AND length(template_snapshot->>'title') BETWEEN 1 AND 200 AND jsonb_typeof(template_snapshot->'points') = 'number' AND (template_snapshot->>'points')::integer BETWEEN 1 AND 10000 AND template_snapshot ? 'estimated_minutes' AND (jsonb_typeof(template_snapshot->'estimated_minutes') = 'null' OR (jsonb_typeof(template_snapshot->'estimated_minutes') = 'number' AND (template_snapshot->>'estimated_minutes')::integer BETWEEN 1 AND 10080)))) IS TRUE",
            name="ck_tasks_template_snapshot",
        ),
        CheckConstraint(
            "(rating_source IS DISTINCT FROM 'TEMPLATE' OR (template_id IS NOT NULL AND rated_points = (template_snapshot->>'points')::integer AND title = template_snapshot->>'title' AND estimated_minutes IS NOT DISTINCT FROM (template_snapshot->>'estimated_minutes')::integer)) IS TRUE",
            name="ck_tasks_template_rating",
        ),
        CheckConstraint("version > 0", name="ck_tasks_version"),
        CheckConstraint(
            "status = 'DONE' OR (completed_at IS NULL AND awarded_points IS NULL)",
            name="ck_tasks_no_uncompleted_award",
        ),
        CheckConstraint(
            "((rated_points IS NULL AND rated_by_user_id IS NULL AND rated_at IS NULL AND rating_source IS NULL) OR (rated_points IS NOT NULL AND rated_at IS NOT NULL AND ((rating_source = 'MANUAL' AND rated_by_user_id IS NOT NULL) OR (rating_source = 'TEMPLATE' AND rated_by_user_id IS NULL)))) IS TRUE",
            name="ck_tasks_rating_complete",
        ),
        CheckConstraint(
            "status NOT IN ('TODO','IN_PROGRESS','DONE') OR rated_points IS NOT NULL",
            name="ck_tasks_active_rated",
        ),
        CheckConstraint(
            "status <> 'BACKLOG' OR rated_points IS NULL", name="ck_tasks_backlog_unrated"
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancelled_at IS NOT NULL", name="ck_tasks_cancelled_at"
        ),
        Index(
            "ix_tasks_active_owner_id",
            "owner_user_id",
            "id",
            postgresql_where=text(
                "deleted_at IS NULL AND status IN ('BACKLOG','TODO','IN_PROGRESS')"
            ),
        ),
        Index(
            "ix_tasks_pending_evaluator_id",
            "evaluator_user_id",
            "id",
            postgresql_where=text("deleted_at IS NULL AND status = 'BACKLOG'"),
        ),
        Index("ix_tasks_owner_status_id", "owner_user_id", "status", "id"),
        Index("ix_tasks_evaluator_status_id", "evaluator_user_id", "status", "id"),
        Index("ix_tasks_shared_status", "shared_user_id", "status", "id"),
        Index("ix_tasks_completed_at", "completed_at"),
    )

    @property
    def participant_user_ids(self) -> tuple[int, ...]:
        return (
            (self.owner_user_id, self.shared_user_id)
            if self.shared_user_id
            else (self.owner_user_id,)
        )


class TaskTemplate(TimestampMixin, Base):
    __tablename__ = "task_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    seed_key: Mapped[str | None] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    operation_key: Mapped[str] = mapped_column(String(80), unique=True)
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_templates_title"),
        CheckConstraint("points BETWEEN 1 AND 10000", name="ck_templates_points"),
        CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080",
            name="ck_templates_effort",
        ),
        CheckConstraint("version > 0", name="ck_templates_version"),
    )


class PenaltyType(TimestampMixin, Base):
    __tablename__ = "penalty_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed_key: Mapped[str | None] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    emoji: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(String(500))
    calculation_type: Mapped[str] = mapped_column(String(20))
    rate: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("calculation_type IN ('PER_MINUTE','FIXED','MANUAL')"),
        CheckConstraint("rate BETWEEN 0 AND 1000000"),
        CheckConstraint("calculation_type = 'MANUAL' OR rate > 0"),
        CheckConstraint("length(name) BETWEEN 1 AND 80", name="ck_penalty_types_name"),
        CheckConstraint("length(emoji) BETWEEN 1 AND 16", name="ck_penalty_types_emoji"),
        CheckConstraint("version > 0", name="ck_penalty_types_version"),
        Index("ix_penalty_types_order", "sort_order", "id"),
    )


class Penalty(TimestampMixin, Base):
    __tablename__ = "penalties"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    penalty_type_id: Mapped[int] = mapped_column(ForeignKey("penalty_types.id"))
    name_snapshot: Mapped[str] = mapped_column(String(80))
    description_snapshot: Mapped[str] = mapped_column(String(500))
    calculation_type_snapshot: Mapped[str] = mapped_column(String(20))
    rate_snapshot: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(1500))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_key: Mapped[str] = mapped_column(String(80), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("points BETWEEN 1 AND 1000000000"),
        CheckConstraint("quantity IS NULL OR quantity BETWEEN 1 AND 10000"),
        CheckConstraint("version > 0", name="ck_penalties_version"),
        CheckConstraint("length(name_snapshot) BETWEEN 1 AND 80", name="ck_penalties_name"),
        CheckConstraint("rate_snapshot BETWEEN 0 AND 1000000", name="ck_penalties_rate"),
        CheckConstraint(
            "(calculation_type_snapshot = 'PER_MINUTE' AND quantity IS NOT NULL AND rate_snapshot > 0 AND points::bigint = quantity::bigint * rate_snapshot) OR (calculation_type_snapshot = 'FIXED' AND quantity IS NULL AND rate_snapshot > 0 AND points = rate_snapshot) OR (calculation_type_snapshot = 'MANUAL' AND quantity IS NULL)",
            name="ck_penalties_calculation",
        ),
        Index("ix_penalties_live_id", "id", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_penalties_target_created", "target_user_id", "created_at"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    old_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    new_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "id"),)


class ScoreEvent(Base):
    __tablename__ = "score_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(10))
    points: Mapped[int] = mapped_column(Integer)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operation_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("category IN ('task','penalty')"),
        Index("ix_score_user_effective", "user_id", "effective_at"),
        Index(
            "ix_score_effective_user",
            "effective_at",
            "user_id",
            postgresql_include=["category", "points"],
        ),
    )


class Dialog(Base):
    __tablename__ = "dialogs"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Inbox(Base):
    __tablename__ = "telegram_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_updates_pending", "status", "available_at", "update_id"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        Index("ix_notifications_due", "status", "available_at"),
        Index(
            "ix_notifications_sending_claimed",
            "claimed_at",
            postgresql_where=text("status = 'sending'"),
        ),
        Index(
            "uq_rating_pending",
            "recipient_user_id",
            unique=True,
            postgresql_where=text("kind = 'rating_pending' AND status = 'pending'"),
        ),
    )
