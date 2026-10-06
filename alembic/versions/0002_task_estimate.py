"""Optional task effort estimate; existing tasks remain unestimated."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("estimated_minutes", sa.Integer(), nullable=True), schema="taskflow"
    )
    op.create_check_constraint(
        "ck_tasks_estimated_minutes",
        "tasks",
        "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080",
        schema="taskflow",
    )


def downgrade() -> None:
    # Keep the additive column and estimates on code rollback. Old code ignores them.
    # A later upgrade can adopt it; dropping user estimates requires an explicit data plan.
    raise RuntimeError(
        "Preserve task estimates; roll back application code or use a forward migration"
    )
