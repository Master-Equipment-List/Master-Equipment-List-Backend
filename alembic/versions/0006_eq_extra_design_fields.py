"""Add extra dimensional / design-condition fields captured from vendor drawings.

These are values every vendor GA drawing prints but the original MEL
template didn't have first-class columns for:

  * ``length_overall_m``            — overall length distinct from T/T
                                       (the reference EPC MEL only has
                                       one "L or T/T" column).
  * ``mdmt_c``                       — Minimum Design Metal Temperature,
                                       usually shown alongside
                                       DESIGN TEMP (e.g. "-40 / 120").
  * ``hydrostatic_test_press_barg`` — hydrotest pressure printed on
                                       every ASME VIII vessel drawing.
  * ``insulation``                   — insulation type + thickness
                                       (free text: "40 mm personal
                                       protection", "75 mm min wool").

All four are TEXT columns so ranges / units / notes survive the round-
trip the same way every other engineering value does.

Revision ID: 0006_eq_extra_design_fields
Revises: 0005_eq_lifecycle_status
Create Date: 2026-07-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_eq_extra_design_fields"
down_revision: Union[str, None] = "0005_eq_lifecycle_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMNS = (
    "length_overall_m",
    "mdmt_c",
    "hydrostatic_test_press_barg",
    "insulation",
)


def upgrade() -> None:
    for name in NEW_COLUMNS:
        op.add_column("equipment", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    for name in NEW_COLUMNS:
        op.drop_column("equipment", name)
