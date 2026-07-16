"""equipment_pending_changes table — per-equipment, per-field sync approval queue

Distinct from the older, never-wired-up ``pending_revisions`` table (which
batched proposed changes per FILE and only covered P&ID). This one is
per-EQUIPMENT-ROW and covers every sync source (PFD/P&ID/Vendor/Excel), so
re-syncing the same tag from any source replaces the single pending
proposal for that row rather than stacking separate entries.

Revision ID: 0007_equipment_pending_changes
Revises: 0006_eq_extra_design_fields
Create Date: 2026-07-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_equipment_pending_changes"
down_revision: Union[str, None] = "0006_eq_extra_design_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "equipment_pending_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.Integer(),
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace", sa.String(16), nullable=False, server_default="topside"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "source_file_id",
            sa.Integer(),
            sa.ForeignKey("project_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposed_fields", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_equipment_pending_changes_equipment_id",
        "equipment_pending_changes",
        ["equipment_id"],
        unique=True,
    )
    op.create_index(
        "ix_equipment_pending_changes_project_id",
        "equipment_pending_changes",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_equipment_pending_changes_project_id", table_name="equipment_pending_changes")
    op.drop_index("ix_equipment_pending_changes_equipment_id", table_name="equipment_pending_changes")
    op.drop_table("equipment_pending_changes")
