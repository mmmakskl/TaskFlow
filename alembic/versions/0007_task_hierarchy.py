"""Add one-level task hierarchy and explicit task links."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("parent_task_id", sa.Integer(), sa.ForeignKey("taskflow.tasks.id")),
        schema="taskflow",
    )
    op.create_check_constraint(
        "ck_tasks_not_own_parent",
        "tasks",
        "parent_task_id IS NULL OR parent_task_id <> id",
        schema="taskflow",
    )
    op.create_index("ix_tasks_parent_id", "tasks", ["parent_task_id", "id"], schema="taskflow")
    op.create_table(
        "task_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_a_id", sa.Integer(), sa.ForeignKey("taskflow.tasks.id"), nullable=False),
        sa.Column("task_b_id", sa.Integer(), sa.ForeignKey("taskflow.tasks.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("taskflow.users.id"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("removed_by_operation_key", sa.String(80), unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("task_a_id <> task_b_id", name="ck_task_links_distinct_tasks"),
        sa.CheckConstraint("kind IN ('RELATED','DEPENDS_ON')", name="ck_task_links_kind"),
        sa.CheckConstraint("version > 0", name="ck_task_links_version"),
        sa.CheckConstraint(
            "(removed_at IS NULL) = (removed_by_operation_key IS NULL)",
            name="ck_task_links_removed_operation",
        ),
        schema="taskflow",
    )
    op.create_index(
        "uq_task_links_active_pair",
        "task_links",
        ["kind", "task_a_id", "task_b_id"],
        unique=True,
        schema="taskflow",
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index("ix_task_links_a", "task_links", ["task_a_id", "id"], schema="taskflow")
    op.create_index("ix_task_links_b", "task_links", ["task_b_id", "id"], schema="taskflow")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM taskflow.task_links)")):
        raise RuntimeError("Task links exist; export them before downgrading revision 0007")
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM taskflow.tasks WHERE parent_task_id IS NOT NULL)")
    ):
        raise RuntimeError("Subtasks exist; export relationships before downgrading revision 0007")
    op.drop_table("task_links", schema="taskflow")
    op.drop_index("ix_tasks_parent_id", table_name="tasks", schema="taskflow")
    op.drop_constraint("ck_tasks_not_own_parent", "tasks", schema="taskflow")
    op.drop_column("tasks", "parent_task_id", schema="taskflow")
