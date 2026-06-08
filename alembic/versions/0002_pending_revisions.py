"""pending_revisions table for drawing-revision approval queue

Revision ID: 0002_pending_revisions
Revises: 0001_initial
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pending_revisions"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_file_id",
            sa.Integer(),
            sa.ForeignKey("project_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("detected_drawing_rev", sa.String(32), nullable=True),
        sa.Column("proposed_changes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column(
            "reviewed_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("apply_outcome", sa.JSON(), nullable=True),
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
        "ix_pending_revisions_project_status",
        "pending_revisions",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_revisions_project_status", table_name="pending_revisions")
    op.drop_table("pending_revisions")
