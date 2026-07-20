"""duplicate_dismissals table — persists "not a duplicate" decisions

The duplicate-audit scan (find_all_duplicate_pairs) is recomputed fresh on
every request and has no memory of its own. Without this table, dismissing
a pair on the Duplicates page only cleared it from local React state — a
page reload (or a later visit) brought the exact same pair right back,
which read as the button "not working". This table is the persisted flag
the audit endpoint now filters against.

Revision ID: 0010_duplicate_dismissals
Revises: 0009_pending_dup_kind
Create Date: 2026-07-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_duplicate_dismissals"
down_revision: Union[str, None] = "0009_pending_dup_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "duplicate_dismissals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace", sa.String(16), nullable=False, server_default="topside"),
        sa.Column(
            "equipment_low_id",
            sa.Integer(),
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "equipment_high_id",
            sa.Integer(),
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dismissed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.UniqueConstraint(
            "project_id", "equipment_low_id", "equipment_high_id",
            name="uq_duplicate_dismissal_pair",
        ),
    )
    op.create_index(
        "ix_duplicate_dismissals_project_id", "duplicate_dismissals", ["project_id"],
    )
    op.create_index(
        "ix_duplicate_dismissals_equipment_low_id", "duplicate_dismissals", ["equipment_low_id"],
    )
    op.create_index(
        "ix_duplicate_dismissals_equipment_high_id", "duplicate_dismissals", ["equipment_high_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_duplicate_dismissals_equipment_high_id", table_name="duplicate_dismissals")
    op.drop_index("ix_duplicate_dismissals_equipment_low_id", table_name="duplicate_dismissals")
    op.drop_index("ix_duplicate_dismissals_project_id", table_name="duplicate_dismissals")
    op.drop_table("duplicate_dismissals")
