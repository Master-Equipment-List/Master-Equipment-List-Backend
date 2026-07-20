"""Records a "not a duplicate" decision from the on-demand duplicate-audit
scan (see app.services.duplicate_detection.find_all_duplicate_pairs).

That scan is recomputed from scratch on every request — it has no memory
of its own. Without this table, dismissing a pair only ever cleared it from
the current page's local state; reload the page (or come back later) and
the exact same pair reappeared, since the fuzzy match itself hasn't
changed. This table is the persisted "seen it, not a duplicate" flag the
audit endpoint filters against before returning results.

The pair is stored with a canonical (low_id, high_id) ordering so the same
physical pair is never stored twice regardless of which order the scan
happened to compare the two rows in.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DuplicateDismissal(Base, TimestampMixin):
    __tablename__ = "duplicate_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "equipment_low_id", "equipment_high_id",
            name="uq_duplicate_dismissal_pair",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace: Mapped[str] = mapped_column(String(16), nullable=False, default="topside")
    # Canonically ordered (low < high) — never store the same pair twice
    # regardless of scan order.
    equipment_low_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    equipment_high_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dismissed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
