"""Per-workspace separation: Topside vs Marine within one project.

Adds a ``workspace`` column to every project-scoped table that holds data
the user expects to see "separately" for Topside vs Marine (equipment,
files, selections, pending revisions). Existing rows are backfilled to
``"topside"`` so nothing disappears.

Also adds per-workspace OneDrive root columns on ``projects`` so each
workspace can point at its own folder. The legacy
``onedrive_root_path / onedrive_root_item_id / onedrive_drive_id``
columns are kept for backward compatibility, and their values are copied
to the new ``topside_*`` columns so the existing project's setup
survives the migration.

Revision ID: 0003_workspaces
Revises: 0002_pending_revisions
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_workspaces"
down_revision: Union[str, None] = "0002_pending_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that hold workspace-scoped data.
_WORKSPACE_TABLES = (
    "equipment",
    "project_files",
    "project_onedrive_selections",
    "pending_revisions",
)


def upgrade() -> None:
    # --- 1. workspace column on each scoped table ---------------------------
    for tbl in _WORKSPACE_TABLES:
        op.add_column(
            tbl,
            sa.Column(
                "workspace",
                sa.String(16),
                nullable=False,
                server_default="topside",
            ),
        )
        op.create_index(f"ix_{tbl}_workspace", tbl, ["workspace"])

    # --- 2. per-workspace OneDrive columns on projects ----------------------
    for prefix in ("topside", "marine"):
        op.add_column(
            "projects",
            sa.Column(f"{prefix}_onedrive_root_path", sa.String(1024), nullable=True),
        )
        op.add_column(
            "projects",
            sa.Column(f"{prefix}_onedrive_root_item_id", sa.String(255), nullable=True),
        )
        op.add_column(
            "projects",
            sa.Column(f"{prefix}_onedrive_drive_id", sa.String(255), nullable=True),
        )

    # --- 3. Backfill: copy legacy onedrive_* into topside_onedrive_* --------
    # Marine columns stay NULL (each project starts with no Marine root).
    op.execute(
        """
        UPDATE projects
           SET topside_onedrive_root_path   = onedrive_root_path,
               topside_onedrive_root_item_id = onedrive_root_item_id,
               topside_onedrive_drive_id    = onedrive_drive_id
         WHERE onedrive_root_path IS NOT NULL
            OR onedrive_root_item_id IS NOT NULL
            OR onedrive_drive_id IS NOT NULL
        """
    )


def downgrade() -> None:
    for prefix in ("marine", "topside"):
        op.drop_column("projects", f"{prefix}_onedrive_drive_id")
        op.drop_column("projects", f"{prefix}_onedrive_root_item_id")
        op.drop_column("projects", f"{prefix}_onedrive_root_path")

    for tbl in _WORKSPACE_TABLES:
        op.drop_index(f"ix_{tbl}_workspace", table_name=tbl)
        op.drop_column(tbl, "workspace")
