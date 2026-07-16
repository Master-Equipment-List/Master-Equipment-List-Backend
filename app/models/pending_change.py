"""Per-equipment pending sync change awaiting admin approval.

Two distinct KINDS of proposal live in this one table:

* ``kind="update"`` (the original / default) — a sync found a tag that
  matches an EXISTING equipment row. The proposed field changes are NOT
  applied immediately; this row holds the field-level diff (old vs new,
  only for fields that actually differ) for a project admin to review.
  ``equipment_id`` is the row being updated.

* ``kind="possible_duplicate"`` — a sync found a tag that does NOT match
  any existing row, but its description + equipment type fuzzy-match an
  EXISTING row under a DIFFERENT tag (see
  ``app.services.duplicate_detection``). Rather than blindly auto-creating
  a second row for what might be the same physical equipment (e.g. a
  vision misread tag, or a genuine tag change), this queues a review: the
  admin sees the candidate side-by-side and decides whether it's really
  new equipment or the same thing under a corrected tag. Here
  ``equipment_id`` is the CANDIDATE existing row (not one being blindly
  updated), ``new_tag`` is the incoming tag that would become a new row's
  ``client_tag`` if confirmed as new, and ``proposed_fields`` diffs the
  candidate's current values against the incoming sync's values.

At most one PENDING row per identity: for ``update`` that's
``equipment_id``; for ``possible_duplicate`` that's ``new_tag`` (so it
doesn't collide with a normal update-kind proposal already pending on the
same candidate row). A newer sync proposing the same thing REPLACES the
still-pending row in place (see ``pending_change_service.py``) rather than
stacking entries. Once resolved, the row is kept as history — a later sync
creates a FRESH row rather than touching the resolved one.

Resolution is per-field for ``update`` (the admin picks, for each proposed
field, whether to keep the existing value or accept the new one — only
accepted fields get written, via the normal ``apply_update`` path, so
precedence rules and ``EquipmentVersion`` snapshots still apply). For
``possible_duplicate`` the admin instead picks ONE of two whole-item
actions: confirm as new equipment (creates it under ``new_tag``) or
confirm as duplicate (merges the incoming fields onto the candidate
``equipment_id`` instead).

``created_by_id`` records who triggered the sync that queued/last-replaced
the proposal; ``resolved_by_id``/``resolved_at`` record who resolved it,
how, and when — kept indefinitely for audit visibility on the Pending
Changes page.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class EquipmentPendingChange(Base, TimestampMixin):
    __tablename__ = "equipment_pending_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "topside" | "marine" — inherited from the equipment row's workspace.
    workspace: Mapped[str] = mapped_column(String(16), nullable=False, default="topside")

    # "pfd" | "pid" | "vendor" | "excel" — whichever sync proposed this.
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True
    )
    # Who triggered the sync that queued/last-replaced this proposal.
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # "update" | "possible_duplicate" — see module docstring.
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="update")
    # Only set for kind="possible_duplicate": the incoming tag that would
    # become the new equipment row's client_tag if confirmed as new.
    new_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # {"field_name": {"old": <value>, "new": <value>}, ...} — only fields
    # whose incoming value differs from the equipment row's current value
    # at the time this proposal was queued/replaced.
    proposed_fields: Mapped[dict] = mapped_column(JSON, nullable=False)

    # "pending" | "approved" | "rejected" (kind="update") or
    # "pending" | "confirmed_new" | "confirmed_duplicate" | "rejected"
    # (kind="possible_duplicate").
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    equipment: Mapped["Equipment"] = relationship()  # noqa: F821
