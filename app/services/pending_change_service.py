"""Per-equipment pending sync change queue.

See ``app.models.pending_change.EquipmentPendingChange`` for the design
rationale. This module holds the operations the rest of the app needs:

  * ``queue_pending_change`` — called by sync_service.py when a sync finds
    a tag that matches an EXISTING equipment row. Diffs the incoming
    fields against the row's current values; if anything differs, upserts
    the currently-PENDING row for that equipment (replacing it — a newer
    sync always wins over an unreviewed one). Resolved (approved/rejected)
    history rows are never touched by this — a fresh sync after a
    resolution creates a brand-new pending row.

  * ``approve_pending_change`` / ``reject_pending_change`` — called by the
    API's approve/reject endpoints. Approving applies the admin-selected
    subset of fields via the normal ``apply_update`` path, so precedence
    rules (P&ID-locking) and the usual ``EquipmentVersion`` snapshot still
    apply exactly as they would for any other update. Either way the row
    is marked resolved (status + resolved_by_id + resolved_at) rather than
    deleted, so who queued/approved/rejected it stays visible.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Equipment, EquipmentPendingChange
from app.services.version_service import TRACKED_FIELDS, apply_update


def _normalize(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


async def queue_pending_change(
    db: AsyncSession,
    equipment: Equipment,
    fields: dict[str, Any],
    *,
    source: str,
    source_file_id: int | None,
    user_id: int | None,
) -> EquipmentPendingChange | None:
    """Diff ``fields`` against ``equipment``'s current values and upsert the
    currently-pending row for whatever actually differs.

    Returns ``None`` (and touches nothing) if every field already matches
    the row's current value — nothing worth putting in front of a reviewer.
    """
    proposed: dict[str, dict[str, Any]] = {}
    for field, new_value in fields.items():
        if field not in TRACKED_FIELDS:
            continue
        new_norm = _normalize(new_value)
        if new_norm is None:
            continue
        old_value = getattr(equipment, field, None)
        if _normalize(old_value) == new_norm:
            continue
        proposed[field] = {"old": old_value, "new": new_value}

    if not proposed:
        return None

    existing = (
        await db.execute(
            select(EquipmentPendingChange).where(
                EquipmentPendingChange.equipment_id == equipment.id,
                EquipmentPendingChange.status == "pending",
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.source = source
        existing.source_file_id = source_file_id
        existing.created_by_id = user_id
        existing.proposed_fields = proposed
        pc = existing
    else:
        pc = EquipmentPendingChange(
            equipment_id=equipment.id,
            project_id=equipment.project_id,
            workspace=equipment.workspace,
            source=source,
            source_file_id=source_file_id,
            created_by_id=user_id,
            proposed_fields=proposed,
        )
        db.add(pc)
    await db.flush()
    return pc


async def approve_pending_change(
    db: AsyncSession,
    pc: EquipmentPendingChange,
    equipment: Equipment,
    *,
    accepted_fields: list[str],
    user_id: int | None,
) -> dict[str, Any]:
    """Apply the admin-selected subset of ``pc.proposed_fields`` — fields
    NOT listed in ``accepted_fields`` simply keep their existing value.
    Applies via the normal ``apply_update`` path, then marks the row
    resolved (kept as history, not deleted).

    Returns which fields were actually applied — this can be a subset of
    ``accepted_fields`` (e.g. a higher-priority source may have locked the
    row since this was queued; ``apply_update`` silently no-ops in that
    case, same as it does for any other caller).
    """
    changes = {
        field: diff["new"]
        for field, diff in pc.proposed_fields.items()
        if field in accepted_fields
    }
    applied_fields: list[str] = []
    if changes:
        version = await apply_update(
            db, equipment, changes,
            source=pc.source,
            source_file_id=pc.source_file_id,
            user_id=user_id,
            note=f"Approved pending {pc.source} change",
        )
        if version:
            applied_fields = [f for f in version.changed_fields if f in changes]

    pc.status = "approved"
    pc.resolved_by_id = user_id
    pc.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return {"applied_fields": applied_fields}


async def reject_pending_change(
    db: AsyncSession, pc: EquipmentPendingChange, *, user_id: int | None
) -> None:
    pc.status = "rejected"
    pc.resolved_by_id = user_id
    pc.resolved_at = datetime.now(timezone.utc)
    await db.flush()
