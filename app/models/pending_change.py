"""Per-equipment pending sync change awaiting admin approval.

When a sync (PFD / P&ID / Vendor Data / Equipment List Excel) finds a tag
that matches an EXISTING equipment row, the proposed field changes are NOT
applied immediately. Instead a row here holds the field-level diff (old vs
new, only for fields that actually differ) for a project admin to review.

New equipment rows (a tag the sync doesn't find yet) are unaffected — since
there's no existing data to overwrite, those still auto-create as before.

At most one row with ``status="pending"`` per ``equipment_id``: if the
same equipment gets synced again while an earlier proposal is still
awaiting review, the newer proposal REPLACES it in place (see
``queue_pending_change`` in ``app.services.pending_change_service``)
rather than stacking entries. Once a row is resolved (approved/rejected)
it's kept as history — a later sync proposing a new change creates a
FRESH row rather than touching the resolved one.

Approval is per-field: the admin picks, for each proposed field, whether
to keep the existing value or accept the new one. Only the accepted
fields get written (via the normal ``apply_update`` path, so the usual
precedence rules and ``EquipmentVersion`` snapshot still apply).
``created_by_id`` records who triggered the sync that queued the
proposal; ``resolved_by_id``/``resolved_at`` record who approved or
rejected it and when — kept indefinitely for audit visibility on the
Pending Changes page.
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

    # {"field_name": {"old": <value>, "new": <value>}, ...} — only fields
    # whose incoming value differs from the equipment row's current value
    # at the time this proposal was queued/replaced.
    proposed_fields: Mapped[dict] = mapped_column(JSON, nullable=False)

    # "pending" | "approved" | "rejected"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    equipment: Mapped["Equipment"] = relationship()  # noqa: F821
