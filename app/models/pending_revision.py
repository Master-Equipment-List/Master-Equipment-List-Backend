"""Pending drawing-revision approval queue.

When a sync extracts proposed equipment changes from a P&ID, the changes
are NOT applied immediately. Instead a ``PendingRevision`` row is created
holding every proposed change for that file. A project editor or admin
reviews the queue, then either:

  - **Approves** the revision  → every proposed change is applied via the
    existing ``apply_update`` / ``create_equipment_from_sync`` helpers,
    generating normal ``EquipmentVersion`` snapshots stamped with the
    reviewer's user id.
  - **Rejects** the revision  → no equipment row is touched. The pending
    row is kept (with status ``rejected``) for audit purposes.

Today only P&IDs gate on approval. PFD, Vendor, and Excel imports
continue to auto-apply.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class PendingRevision(Base, TimestampMixin):
    __tablename__ = "pending_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # "topside" | "marine" — inherited from the source file's workspace.
    workspace: Mapped[str] = mapped_column(
        String(16), nullable=False, default="topside", index=True
    )
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True
    )

    # "pid" today; other sources later if we extend gating.
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # The drawing's printed revision (e.g. "D1", "Z1", "00") parsed from
    # the title block. Informational — used for the UI badge.
    detected_drawing_rev: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # JSON list of proposed changes:
    #   [{"client_equipment_tag": "V-S67105",
    #     "fields": {...},
    #     "is_new_equipment": false,
    #     "current_values": {...}  // for the reviewer's diff display
    #   }, ...]
    proposed_changes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # "pending" | "approved" | "rejected"
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)

    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Snapshot of the outcome when approved, for visibility:
    #   {"applied": 11, "skipped": 0, "errors": []}
    apply_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
