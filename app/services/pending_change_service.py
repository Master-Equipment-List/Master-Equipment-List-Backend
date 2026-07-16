"""Per-equipment pending sync change queue.

See ``app.models.pending_change.EquipmentPendingChange`` for the design
rationale. This module holds the operations the rest of the app needs:

  * ``queue_pending_change`` — called by sync_service.py when a sync finds
    a tag that matches an EXISTING equipment row. Diffs the incoming
    fields against the row's current values; if anything differs, upserts
    the currently-PENDING row for that equipment (replacing it — a newer
    sync always wins over an unreviewed one). Resolved history rows are
    never touched by this — a fresh sync after a resolution creates a
    brand-new pending row.

  * ``approve_pending_change`` / ``reject_pending_change`` — called by the
    API's approve/reject endpoints for ``kind="update"`` rows. Approving
    applies the admin-selected subset of fields via the normal
    ``apply_update`` path, so precedence rules (P&ID-locking) and the usual
    ``EquipmentVersion`` snapshot still apply exactly as they would for any
    other update. Either way the row is marked resolved rather than
    deleted, so who queued/resolved it stays visible.

  * ``queue_possible_duplicate`` / ``resolve_duplicate_as_new`` /
    ``resolve_duplicate_as_merge`` — the ``kind="possible_duplicate"``
    counterparts (see the model docstring): a tag that didn't match
    anything, but fuzzy-matched an existing row's description + type. The
    admin picks exactly one of the two whole-item resolutions instead of a
    per-field approve.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Equipment, EquipmentPendingChange
from app.services.equipment_create_helper import create_equipment_from_sync
from app.services.version_service import TRACKED_FIELDS, apply_update


def _normalize(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _compute_diff(
    equipment: Equipment, fields: dict[str, Any], *, exclude: tuple[str, ...] = ()
) -> dict[str, dict[str, Any]]:
    """Fields in ``fields`` whose value actually differs from ``equipment``'s
    current value — the shape both proposal kinds store in
    ``proposed_fields``."""
    proposed: dict[str, dict[str, Any]] = {}
    for field, new_value in fields.items():
        if field not in TRACKED_FIELDS or field in exclude:
            continue
        new_norm = _normalize(new_value)
        if new_norm is None:
            continue
        old_value = getattr(equipment, field, None)
        if _normalize(old_value) == new_norm:
            continue
        proposed[field] = {"old": old_value, "new": new_value}
    return proposed


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
    proposed = _compute_diff(equipment, fields)
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


async def _apply_accepted_fields(
    db: AsyncSession,
    pc: EquipmentPendingChange,
    equipment: Equipment,
    *,
    accepted_fields: list[str],
    user_id: int | None,
    note: str,
) -> list[str]:
    """Apply the admin-selected subset of ``pc.proposed_fields`` onto
    ``equipment`` via the normal ``apply_update`` path — fields NOT listed
    in ``accepted_fields`` simply keep their existing value. Returns which
    fields were actually applied (can be a subset of ``accepted_fields``,
    e.g. a higher-priority source may have locked the row since this was
    queued; ``apply_update`` silently no-ops in that case).
    """
    changes = {
        field: diff["new"]
        for field, diff in pc.proposed_fields.items()
        if field in accepted_fields
    }
    if not changes:
        return []
    version = await apply_update(
        db, equipment, changes,
        source=pc.source,
        source_file_id=pc.source_file_id,
        user_id=user_id,
        note=note,
    )
    return [f for f in version.changed_fields if f in changes] if version else []


def _mark_resolved(pc: EquipmentPendingChange, *, status: str, user_id: int | None) -> None:
    pc.status = status
    pc.resolved_by_id = user_id
    pc.resolved_at = datetime.now(timezone.utc)


async def approve_pending_change(
    db: AsyncSession,
    pc: EquipmentPendingChange,
    equipment: Equipment,
    *,
    accepted_fields: list[str],
    user_id: int | None,
) -> dict[str, Any]:
    """Resolve a ``kind="update"`` proposal: apply the admin-selected subset
    of fields, then mark the row resolved (kept as history, not deleted)."""
    applied_fields = await _apply_accepted_fields(
        db, pc, equipment,
        accepted_fields=accepted_fields, user_id=user_id,
        note=f"Approved pending {pc.source} change",
    )
    _mark_resolved(pc, status="approved", user_id=user_id)
    await db.flush()
    return {"applied_fields": applied_fields}


async def reject_pending_change(
    db: AsyncSession, pc: EquipmentPendingChange, *, user_id: int | None
) -> None:
    """Discard any proposal (either kind) without touching equipment data."""
    _mark_resolved(pc, status="rejected", user_id=user_id)
    await db.flush()


# ---------------------------------------------------------------------------
# kind="possible_duplicate" — see the model docstring for the full picture.
# ---------------------------------------------------------------------------

async def queue_possible_duplicate(
    db: AsyncSession,
    candidate: Equipment,
    new_tag: str,
    fields: dict[str, Any],
    *,
    source: str,
    source_file_id: int | None,
    user_id: int | None,
) -> EquipmentPendingChange | None:
    """A sync reported ``new_tag`` (no existing row has that tag), but its
    description + equipment type fuzzy-matched ``candidate`` closely enough
    to be worth a human's judgment call. Diffs ``fields`` against
    ``candidate``'s current values (excluding ``client_tag`` — the mismatch
    IS the point, shown via ``new_tag`` rather than as an ordinary field
    diff) and upserts the currently-pending duplicate-check for this
    ``new_tag`` (keyed by tag, not by candidate id, so it doesn't collide
    with a normal ``kind="update"`` proposal that might already be pending
    on the same candidate row from an unrelated, correctly-tag-matched
    sync).

    Returns ``None`` if there's nothing to diff (shouldn't normally happen,
    since a fuzzy-matched candidate almost always differs somewhere).
    """
    proposed = _compute_diff(candidate, fields, exclude=("client_tag",))
    if not proposed:
        return None

    existing = (
        await db.execute(
            select(EquipmentPendingChange).where(
                EquipmentPendingChange.project_id == candidate.project_id,
                EquipmentPendingChange.workspace == candidate.workspace,
                EquipmentPendingChange.kind == "possible_duplicate",
                EquipmentPendingChange.new_tag == new_tag,
                EquipmentPendingChange.status == "pending",
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.equipment_id = candidate.id
        existing.source = source
        existing.source_file_id = source_file_id
        existing.created_by_id = user_id
        existing.proposed_fields = proposed
        pc = existing
    else:
        pc = EquipmentPendingChange(
            equipment_id=candidate.id,
            project_id=candidate.project_id,
            workspace=candidate.workspace,
            source=source,
            source_file_id=source_file_id,
            created_by_id=user_id,
            kind="possible_duplicate",
            new_tag=new_tag,
            proposed_fields=proposed,
        )
        db.add(pc)
    await db.flush()
    return pc


async def resolve_duplicate_as_new(
    db: AsyncSession,
    pc: EquipmentPendingChange,
    project_id: int,
    *,
    user_id: int | None,
) -> Equipment:
    """Admin confirmed: this is genuinely new equipment, not a duplicate.
    Creates it under ``pc.new_tag`` with every proposed "new" value (the
    full incoming field set — unlike the merge path, there's no partial
    accept here, since there's no existing row's values to preserve)."""
    fields = {field: diff["new"] for field, diff in pc.proposed_fields.items()}
    eq = await create_equipment_from_sync(
        db, project_id, pc.new_tag, fields,
        source=pc.source,
        source_file_id=pc.source_file_id,
        user_id=user_id,
        workspace=pc.workspace,
    )
    _mark_resolved(pc, status="confirmed_new", user_id=user_id)
    await db.flush()
    return eq


async def resolve_duplicate_as_merge(
    db: AsyncSession,
    pc: EquipmentPendingChange,
    candidate: Equipment,
    *,
    accepted_fields: list[str],
    user_id: int | None,
) -> dict[str, Any]:
    """Admin confirmed: this IS the same equipment as ``candidate``, just
    under a different/corrected tag. Applies the admin-selected subset of
    fields onto ``candidate`` (the tag itself is never renamed — the
    candidate keeps its own existing client_tag) and marks resolved."""
    applied_fields = await _apply_accepted_fields(
        db, pc, candidate,
        accepted_fields=accepted_fields, user_id=user_id,
        note=f"Confirmed duplicate of incoming tag {pc.new_tag} ({pc.source})",
    )
    _mark_resolved(pc, status="confirmed_duplicate", user_id=user_id)
    await db.flush()
    return {"applied_fields": applied_fields}
