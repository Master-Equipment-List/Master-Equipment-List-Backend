"""Add lifecycle_status column to equipment.

Captures the Marine MEL's "SCRAPPED / REFURBISHED / NEW" dropdown
columns. We collapse those three Excel columns into ONE string so the
detail and list views can render a single status badge. Existing rows
get NULL (they were imported before this column existed and the
information isn't recoverable from the existing data).

Revision ID: 0005_eq_lifecycle_status
Revises: 0004_eq_workspace_tag_unique
Create Date: 2026-06-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_eq_lifecycle_status"
down_revision: Union[str, None] = "0004_eq_workspace_tag_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "equipment",
        sa.Column("lifecycle_status", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("equipment", "lifecycle_status")
