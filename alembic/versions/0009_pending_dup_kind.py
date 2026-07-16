"""equipment_pending_changes: add kind + new_tag for possible-duplicate proposals

Also widens `status` from 16 to 24 chars to fit "confirmed_duplicate".

Revision ID: 0009_pending_dup_kind
Revises: 0008_pending_change_history
Create Date: 2026-07-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_pending_dup_kind"
down_revision: Union[str, None] = "0008_pending_change_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "equipment_pending_changes",
        sa.Column("kind", sa.String(24), nullable=False, server_default="update"),
    )
    op.add_column(
        "equipment_pending_changes",
        sa.Column("new_tag", sa.String(255), nullable=True),
    )
    op.alter_column(
        "equipment_pending_changes",
        "status",
        existing_type=sa.String(16),
        type_=sa.String(24),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "equipment_pending_changes",
        "status",
        existing_type=sa.String(24),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.drop_column("equipment_pending_changes", "new_tag")
    op.drop_column("equipment_pending_changes", "kind")
