"""Widen the equipment unique constraint to include workspace.

The original constraint ``uq_equipment_project_tag`` was ``(project_id,
client_tag)``. With per-workspace MELs that's too tight: Topsides
"P-F16030" and Marine "P-F16030" are unrelated equipment items in
different equipment lists for the same project, and the user should be
able to have both.

This migration:
  1. Drops the old constraint.
  2. Adds ``uq_equipment_project_workspace_tag`` on
     ``(project_id, workspace, client_tag)``.

It assumes ``workspace`` has already been backfilled to ``"topside"`` on
all existing rows by ``0003_workspaces`` — true for every row that
existed before workspaces shipped.

Revision ID: 0004_eq_workspace_tag_unique
Revises: 0003_workspaces
Create Date: 2026-06-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_eq_workspace_tag_unique"
down_revision: Union[str, None] = "0003_workspaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_equipment_project_tag", "equipment", type_="unique"
    )
    op.create_unique_constraint(
        "uq_equipment_project_workspace_tag",
        "equipment",
        ["project_id", "workspace", "client_tag"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_equipment_project_workspace_tag", "equipment", type_="unique"
    )
    op.create_unique_constraint(
        "uq_equipment_project_tag",
        "equipment",
        ["project_id", "client_tag"],
    )
