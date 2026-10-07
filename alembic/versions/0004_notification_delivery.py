"""Durable notification delivery diagnostics; no replay of old attempts."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("claimed_at", "finished_at"):
        op.add_column(
            "notifications", sa.Column(name, sa.DateTime(timezone=True)), schema="taskflow"
        )
    op.add_column(
        "notifications",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        schema="taskflow",
    )
    op.add_column("notifications", sa.Column("error_code", sa.String(40)), schema="taskflow")
    # Old sending rows have an unknown outcome. Never automatically replay them.
    op.execute(
        "UPDATE taskflow.notifications SET status='uncertain', error_code='legacy_inflight' WHERE status='sending'"
    )
    op.create_index(
        "ix_notifications_sending_claimed",
        "notifications",
        ["claimed_at"],
        schema="taskflow",
        postgresql_where=sa.text("status = 'sending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_sending_claimed", table_name="notifications", schema="taskflow")
    for name in ("error_code", "attempts", "finished_at", "claimed_at"):
        op.drop_column("notifications", name, schema="taskflow")
