"""Collaborative tasks, immutable participants and reusable templates.

Rollback requires exporting new records and restoring a pre-upgrade backup. Old
workers must be stopped before upgrade; their rating constraints are incompatible.
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "creator_user_id", sa.Integer(), sa.ForeignKey("taskflow.users.id"), nullable=False
        ),
        sa.Column("seed_key", sa.String(32), unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("estimated_minutes", sa.Integer()),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_templates_title"),
        sa.CheckConstraint("points BETWEEN 1 AND 10000", name="ck_templates_points"),
        sa.CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080",
            name="ck_templates_effort",
        ),
        sa.CheckConstraint("version > 0", name="ck_templates_version"),
        schema="taskflow",
    )
    columns = [
        sa.Column("creator_user_id", sa.Integer(), sa.ForeignKey("taskflow.users.id")),
        sa.Column("kind", sa.String(20), nullable=False, server_default="PERSONAL"),
        sa.Column("shared_user_id", sa.Integer(), sa.ForeignKey("taskflow.users.id")),
        sa.Column("proposed_points", sa.Integer()),
        sa.Column("proposal_by_user_id", sa.Integer(), sa.ForeignKey("taskflow.users.id")),
        sa.Column("first_accepted_at", sa.DateTime(timezone=True)),
        sa.Column("rating_source", sa.String(20)),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("taskflow.task_templates.id")),
        sa.Column("template_version", sa.Integer()),
        sa.Column("template_snapshot", sa.dialects.postgresql.JSONB()),
    ]
    for column in columns:
        op.add_column("tasks", column, schema="taskflow")
    op.execute("""UPDATE taskflow.tasks SET creator_user_id=owner_user_id,
        proposal_by_user_id=owner_user_id, first_accepted_at=rated_at,
        rating_source=CASE WHEN rated_points IS NOT NULL THEN 'MANUAL' END""")
    for name in ("creator_user_id", "proposal_by_user_id"):
        op.alter_column("tasks", name, nullable=False, schema="taskflow")
    # Generated PostgreSQL names for the original unnamed checks vary across
    # installations; inspect definitions rather than assuming a generated name.
    op.execute("""DO $$ DECLARE c record; BEGIN
        FOR c IN SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
        WHERE conrelid='taskflow.tasks'::regclass AND contype='c' LOOP
        IF c.def LIKE '%owner_user_id <> evaluator_user_id%' THEN
            EXECUTE format('ALTER TABLE taskflow.tasks DROP CONSTRAINT %I', c.conname);
        END IF; END LOOP; END $$""")
    op.drop_constraint("ck_tasks_rating_complete", "tasks", schema="taskflow")
    checks = {
        "ck_tasks_kind": "kind IN ('PERSONAL','SHARED')",
        "ck_tasks_participants": "(kind = 'PERSONAL' AND shared_user_id IS NULL) OR (kind = 'SHARED' AND shared_user_id IS NOT NULL AND shared_user_id <> owner_user_id)",
        "ck_tasks_other_approver": "evaluator_user_id <> proposal_by_user_id",
        "ck_tasks_proposed_points": "proposed_points IS NULL OR proposed_points BETWEEN 1 AND 10000",
        "ck_tasks_rating_complete": "((rated_points IS NULL AND rated_by_user_id IS NULL AND rated_at IS NULL AND rating_source IS NULL) OR (rated_points IS NOT NULL AND rated_at IS NOT NULL AND ((rating_source = 'MANUAL' AND rated_by_user_id IS NOT NULL) OR (rating_source = 'TEMPLATE' AND rated_by_user_id IS NULL)))) IS TRUE",
        "ck_tasks_template_snapshot": "((template_id IS NULL AND template_version IS NULL AND template_snapshot IS NULL) OR (template_id IS NOT NULL AND template_version IS NOT NULL AND template_version > 0 AND template_snapshot IS NOT NULL AND jsonb_typeof(template_snapshot) = 'object' AND jsonb_typeof(template_snapshot->'title') = 'string' AND length(template_snapshot->>'title') BETWEEN 1 AND 200 AND jsonb_typeof(template_snapshot->'points') = 'number' AND (template_snapshot->>'points')::integer BETWEEN 1 AND 10000 AND template_snapshot ? 'estimated_minutes' AND (jsonb_typeof(template_snapshot->'estimated_minutes') = 'null' OR (jsonb_typeof(template_snapshot->'estimated_minutes') = 'number' AND (template_snapshot->>'estimated_minutes')::integer BETWEEN 1 AND 10080)))) IS TRUE",
        "ck_tasks_template_rating": "(rating_source IS DISTINCT FROM 'TEMPLATE' OR (template_id IS NOT NULL AND rated_points = (template_snapshot->>'points')::integer AND title = template_snapshot->>'title' AND estimated_minutes IS NOT DISTINCT FROM (template_snapshot->>'estimated_minutes')::integer)) IS TRUE",
    }
    for name, expression in checks.items():
        op.create_check_constraint(name, "tasks", expression, schema="taskflow")
    op.create_index(
        "ix_tasks_shared_status", "tasks", ["shared_user_id", "status", "id"], schema="taskflow"
    )
    op.execute("""CREATE FUNCTION taskflow.guard_task_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.creator_user_id IS DISTINCT FROM OLD.creator_user_id
        OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
        OR NEW.shared_user_id IS DISTINCT FROM OLD.shared_user_id
        OR NEW.kind IS DISTINCT FROM OLD.kind
        OR NEW.operation_key IS DISTINCT FROM OLD.operation_key
        OR NEW.template_id IS DISTINCT FROM OLD.template_id
        OR NEW.template_version IS DISTINCT FROM OLD.template_version
        OR NEW.template_snapshot IS DISTINCT FROM OLD.template_snapshot
        OR (OLD.first_accepted_at IS NOT NULL AND NEW.first_accepted_at IS DISTINCT FROM OLD.first_accepted_at)
        THEN RAISE EXCEPTION 'task identity and original snapshots are immutable'; END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER task_identity_immutable BEFORE UPDATE ON taskflow.tasks FOR EACH ROW EXECUTE FUNCTION taskflow.guard_task_identity()"
    )


def downgrade() -> None:
    raise RuntimeError("0005 has no destructive downgrade; restore the reviewed pre-upgrade backup")
