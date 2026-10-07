"""Business invariants, immutable penalty snapshots and query indexes."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CONSTRAINTS = {
    "users": {
        "ck_users_telegram_positive": "telegram_id > 0",
        "ck_users_name": "length(display_name) BETWEEN 1 AND 50",
    },
    "tasks": {
        "ck_tasks_version": "version > 0",
        "ck_tasks_no_uncompleted_award": "status = 'DONE' OR (completed_at IS NULL AND awarded_points IS NULL)",
        "ck_tasks_rating_complete": "(rated_points IS NULL AND rated_by_user_id IS NULL AND rated_at IS NULL) OR (rated_points IS NOT NULL AND rated_by_user_id IS NOT NULL AND rated_at IS NOT NULL)",
        "ck_tasks_active_rated": "status NOT IN ('TODO','IN_PROGRESS','DONE') OR rated_points IS NOT NULL",
        "ck_tasks_backlog_unrated": "status <> 'BACKLOG' OR rated_points IS NULL",
        "ck_tasks_cancelled_at": "status <> 'CANCELLED' OR cancelled_at IS NOT NULL",
    },
    "penalty_types": {
        "ck_penalty_types_name": "length(name) BETWEEN 1 AND 80",
        "ck_penalty_types_emoji": "length(emoji) BETWEEN 1 AND 16",
        "ck_penalty_types_version": "version > 0",
    },
    "penalties": {
        "ck_penalties_version": "version > 0",
        "ck_penalties_name": "length(name_snapshot) BETWEEN 1 AND 80",
        "ck_penalties_rate": "rate_snapshot BETWEEN 0 AND 1000000",
        "ck_penalties_calculation": "(calculation_type_snapshot = 'PER_MINUTE' AND quantity IS NOT NULL AND rate_snapshot > 0 AND points::bigint = quantity::bigint * rate_snapshot) OR (calculation_type_snapshot = 'FIXED' AND quantity IS NULL AND rate_snapshot > 0 AND points = rate_snapshot) OR (calculation_type_snapshot = 'MANUAL' AND quantity IS NULL)",
    },
}
INDEXES = {
    "ix_tasks_active_owner_id": "ON taskflow.tasks (owner_user_id, id) WHERE deleted_at IS NULL AND status IN ('BACKLOG','TODO','IN_PROGRESS')",
    "ix_tasks_pending_evaluator_id": "ON taskflow.tasks (evaluator_user_id, id) WHERE deleted_at IS NULL AND status = 'BACKLOG'",
    "ix_penalties_live_id": "ON taskflow.penalties (id) WHERE deleted_at IS NULL",
    "ix_penalty_types_order": "ON taskflow.penalty_types (sort_order, id)",
    "ix_score_effective_user": "ON taskflow.score_events (effective_at, user_id) INCLUDE (category, points)",
}


def upgrade() -> None:
    # NOT VALID avoids the table scan while obtaining the initial DDL lock.
    # VALIDATE checks existing data and aborts the migration instead of rewriting it.
    for table, constraints in CONSTRAINTS.items():
        for name, expression in constraints.items():
            op.execute(
                f"ALTER TABLE taskflow.{table} ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
            )
            op.execute(f"ALTER TABLE taskflow.{table} VALIDATE CONSTRAINT {name}")
    for name, expression in INDEXES.items():
        op.execute(f"CREATE INDEX {name} {expression}")
    op.execute("""
        CREATE FUNCTION taskflow.reject_penalty_snapshot_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF ROW(NEW.target_user_id,NEW.created_by_user_id,NEW.penalty_type_id,
                 NEW.name_snapshot,NEW.description_snapshot,NEW.calculation_type_snapshot,
                 NEW.rate_snapshot,NEW.quantity,NEW.points,NEW.comment,NEW.created_at,NEW.operation_key)
             IS DISTINCT FROM
             ROW(OLD.target_user_id,OLD.created_by_user_id,OLD.penalty_type_id,
                 OLD.name_snapshot,OLD.description_snapshot,OLD.calculation_type_snapshot,
                 OLD.rate_snapshot,OLD.quantity,OLD.points,OLD.comment,OLD.created_at,OLD.operation_key)
          THEN RAISE EXCEPTION 'Penalty snapshots are immutable'; END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER penalties_snapshot_immutable BEFORE UPDATE ON taskflow.penalties FOR EACH ROW EXECUTE FUNCTION taskflow.reject_penalty_snapshot_mutation()"
    )


def downgrade() -> None:
    # Reversible enforcement only: preserve every row, snapshot, ledger and audit.
    op.execute("DROP TRIGGER penalties_snapshot_immutable ON taskflow.penalties")
    op.execute("DROP FUNCTION taskflow.reject_penalty_snapshot_mutation()")
    for name in reversed(INDEXES):
        op.drop_index(name, schema="taskflow")
    for table, constraints in CONSTRAINTS.items():
        for name in constraints:
            op.drop_constraint(name, table, type_="check", schema="taskflow")
