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
    __table_args__ = (CheckConstraint("weekly_target > 0"),)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    evaluator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
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
        CheckConstraint("owner_user_id <> evaluator_user_id"),
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
        Index("ix_tasks_owner_status_id", "owner_user_id", "status", "id"),
        Index("ix_tasks_evaluator_status_id", "evaluator_user_id", "status", "id"),
        Index("ix_tasks_completed_at", "completed_at"),
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
    __table_args__ = (
        Index("ix_notifications_due", "status", "available_at"),
        Index(
            "uq_rating_pending",
            "recipient_user_id",
            unique=True,
            postgresql_where=text("kind = 'rating_pending' AND status = 'pending'"),
        ),
    )
