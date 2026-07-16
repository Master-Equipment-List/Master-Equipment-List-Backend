"""equipment_pending_changes: add status/attribution, drop unique(equipment_id)

Turns the table from "delete on resolve" into a kept history: resolved
(approved/rejected) rows now stay for audit visibility instead of being
deleted, so equipment_id can no longer be unique (a row accumulates one
history entry per resolved sync, plus at most one still-pending row).

Revision ID: 0008_pending_change_history
Revises: 0007_equipment_pending_changes
Create Date: 2026-07-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_pending_change_history"
down_revision: Union[str, None] = "0007_equipment_pending_changes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_equipment_pending_changes_equipment_id", table_name="equipment_pending_changes")
    op.create_index(
        "ix_equipment_pending_changes_equipment_id",
        "equipment_pending_changes",
        ["equipment_id"],
        unique=False,
    )

    op.add_column(
        "equipment_pending_changes",
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "equipment_pending_changes",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "equipment_pending_changes",
        sa.Column(
            "resolved_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "equipment_pending_changes",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_equipment_pending_changes_status",
        "equipment_pending_changes",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_pending_changes_status", table_name="equipment_pending_changes")
    op.drop_column("equipment_pending_changes", "resolved_at")
    op.drop_column("equipment_pending_changes", "resolved_by_id")
    op.drop_column("equipment_pending_changes", "status")
    op.drop_column("equipment_pending_changes", "created_by_id")

    op.drop_index("ix_equipment_pending_changes_equipment_id", table_name="equipment_pending_changes")
    op.create_index(
        "ix_equipment_pending_changes_equipment_id",
        "equipment_pending_changes",
        ["equipment_id"],
        unique=True,
    )
