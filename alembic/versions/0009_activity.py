"""Add shared tags and private activity boards.

Downgrade is refused while tag or board configuration exists.
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def timestamps():
        return [
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ]

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("normalized_name", sa.String(80), nullable=False, unique=True),
        sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        *timestamps(),
        schema="taskflow",
    )
    op.create_table(
        "task_tags",
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("taskflow.tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("taskflow.tags.id"), primary_key=True),
        schema="taskflow",
    )
    op.create_table(
        "template_tags",
        sa.Column(
            "template_id",
            sa.Integer,
            sa.ForeignKey("taskflow.task_templates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("taskflow.tags.id"), primary_key=True),
        schema="taskflow",
    )
    op.create_table(
        "activity_boards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("taskflow.users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        *timestamps(),
        schema="taskflow",
    )
    op.create_table(
        "activity_trackers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "board_id",
            sa.Integer,
            sa.ForeignKey("taskflow.activity_boards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("metric", sa.String(12), nullable=False),
        sa.Column("unit", sa.String(40)),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("operation_key", sa.String(80), nullable=False, unique=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        *timestamps(),
        sa.CheckConstraint("metric IN ('TASKS','TIME','QUANTITY')", name="ck_trackers_metric"),
        sa.CheckConstraint(
            "(metric = 'QUANTITY' AND unit IS NOT NULL AND length(unit) BETWEEN 1 AND 40) OR (metric <> 'QUANTITY' AND unit IS NULL)",
            name="ck_trackers_unit",
        ),
        schema="taskflow",
    )
    op.create_table(
        "tracker_tags",
        sa.Column(
            "tracker_id",
            sa.Integer,
            sa.ForeignKey("taskflow.activity_trackers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("taskflow.tags.id"), primary_key=True),
        schema="taskflow",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM taskflow.tags) OR EXISTS (SELECT 1 FROM taskflow.activity_boards)"
        )
    ):
        raise RuntimeError("Activity configuration exists; export it before downgrade")
    for name in (
        "tracker_tags",
        "activity_trackers",
        "activity_boards",
        "template_tags",
        "task_tags",
        "tags",
    ):
        op.drop_table(name, schema="taskflow")
