"""Support fixed and volume-based task template pricing."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SNAPSHOT_CHECK = """((template_id IS NULL AND template_version IS NULL AND template_snapshot IS NULL) OR (
    template_id IS NOT NULL AND template_version IS NOT NULL AND template_version > 0
    AND template_snapshot IS NOT NULL AND jsonb_typeof(template_snapshot) = 'object'
    AND jsonb_typeof(template_snapshot->'title') = 'string'
    AND length(template_snapshot->>'title') BETWEEN 1 AND 200
    AND jsonb_typeof(template_snapshot->'points') = 'number'
    AND (template_snapshot->>'points')::integer BETWEEN 1 AND 10000
    AND template_snapshot ? 'estimated_minutes'
    AND (jsonb_typeof(template_snapshot->'estimated_minutes') = 'null' OR
        (jsonb_typeof(template_snapshot->'estimated_minutes') = 'number'
            AND (template_snapshot->>'estimated_minutes')::integer BETWEEN 1 AND 10080))
    AND (NOT (template_snapshot ? 'pricing_mode') OR (
        template_snapshot->>'pricing_mode' IN ('FIXED','PER_TIME','PER_QUANTITY')
        AND jsonb_typeof(template_snapshot->'base_points') = 'number'
        AND (template_snapshot->>'base_points')::integer BETWEEN 1 AND 10000
        AND template_snapshot ? 'base_quantity' AND template_snapshot ? 'unit_label'
        AND template_snapshot ? 'quantity'
        AND ((template_snapshot->>'pricing_mode' = 'FIXED'
            AND template_snapshot->'base_quantity' = 'null'::jsonb
            AND template_snapshot->'unit_label' = 'null'::jsonb
            AND template_snapshot->'quantity' = 'null'::jsonb
            AND (template_snapshot->>'points')::integer = (template_snapshot->>'base_points')::integer)
          OR (template_snapshot->>'pricing_mode' = 'PER_TIME'
            AND jsonb_typeof(template_snapshot->'base_quantity') = 'number'
            AND (template_snapshot->>'base_quantity')::integer BETWEEN 1 AND 10080
            AND template_snapshot->'unit_label' = 'null'::jsonb
            AND jsonb_typeof(template_snapshot->'quantity') = 'number'
            AND (template_snapshot->>'quantity')::integer BETWEEN 1 AND 10080)
          OR (template_snapshot->>'pricing_mode' = 'PER_QUANTITY'
            AND jsonb_typeof(template_snapshot->'base_quantity') = 'number'
            AND (template_snapshot->>'base_quantity')::integer BETWEEN 1 AND 10000
            AND jsonb_typeof(template_snapshot->'unit_label') = 'string'
            AND length(template_snapshot->>'unit_label') BETWEEN 1 AND 40
            AND jsonb_typeof(template_snapshot->'quantity') = 'number'
            AND (template_snapshot->>'quantity')::integer BETWEEN 1 AND 10000))
    ))
)) IS TRUE"""


def upgrade() -> None:
    op.add_column(
        "task_templates",
        sa.Column("pricing_mode", sa.String(20), nullable=False, server_default="FIXED"),
        schema="taskflow",
    )
    op.add_column("task_templates", sa.Column("base_quantity", sa.Integer()), schema="taskflow")
    op.add_column("task_templates", sa.Column("unit_label", sa.String(40)), schema="taskflow")
    op.create_check_constraint(
        "ck_templates_pricing",
        "task_templates",
        "(pricing_mode = 'FIXED' AND base_quantity IS NULL AND unit_label IS NULL) OR "
        "(pricing_mode = 'PER_TIME' AND base_quantity BETWEEN 1 AND 10080 AND unit_label IS NULL) OR "
        "(pricing_mode = 'PER_QUANTITY' AND base_quantity BETWEEN 1 AND 10000 "
        "AND unit_label IS NOT NULL AND length(unit_label) BETWEEN 1 AND 40)",
        schema="taskflow",
    )

    op.drop_constraint("ck_tasks_template_snapshot", "tasks", schema="taskflow")
    op.create_check_constraint(
        "ck_tasks_template_snapshot", "tasks", SNAPSHOT_CHECK, schema="taskflow"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM taskflow.task_templates WHERE pricing_mode <> 'FIXED')"
        )
    ):
        raise RuntimeError("Volume-priced templates exist; convert or export them before downgrade")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM taskflow.tasks WHERE template_snapshot ? 'pricing_mode')"
        )
    ):
        raise RuntimeError("Template snapshots use the new format; export them before downgrade")
    op.drop_constraint("ck_tasks_template_snapshot", "tasks", schema="taskflow")
    op.create_check_constraint(
        "ck_tasks_template_snapshot",
        "tasks",
        "((template_id IS NULL AND template_version IS NULL AND template_snapshot IS NULL) OR "
        "(template_id IS NOT NULL AND template_version IS NOT NULL AND template_version > 0 "
        "AND template_snapshot IS NOT NULL AND jsonb_typeof(template_snapshot) = 'object' "
        "AND jsonb_typeof(template_snapshot->'title') = 'string' "
        "AND length(template_snapshot->>'title') BETWEEN 1 AND 200 "
        "AND jsonb_typeof(template_snapshot->'points') = 'number' "
        "AND (template_snapshot->>'points')::integer BETWEEN 1 AND 10000 "
        "AND template_snapshot ? 'estimated_minutes' AND "
        "(jsonb_typeof(template_snapshot->'estimated_minutes') = 'null' OR "
        "(jsonb_typeof(template_snapshot->'estimated_minutes') = 'number' AND "
        "(template_snapshot->>'estimated_minutes')::integer BETWEEN 1 AND 10080)))) IS TRUE",
        schema="taskflow",
    )
    op.drop_constraint("ck_templates_pricing", "task_templates", schema="taskflow")
    op.drop_column("task_templates", "unit_label", schema="taskflow")
    op.drop_column("task_templates", "base_quantity", schema="taskflow")
    op.drop_column("task_templates", "pricing_mode", schema="taskflow")
