"""Equipment update + version snapshot helper."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Equipment, EquipmentVersion


# Source precedence — higher index wins. Once an equipment row has been
# touched by a higher-priority source, lower-priority sources can't
# overwrite it (their apply_update calls return None instead of changing
# fields). Manual edits and Excel imports are ALWAYS allowed — they're
# the engineer's explicit override channel.
_SOURCE_PRIORITY = {
    "seed":   0,
    "pfd":    1,
    "vendor": 2,
    "pid":    3,
    "repair": 99,  # internal — back-fill / fix-up; bypasses precedence
    "manual": 99,
    "excel":  99,
}


async def _has_higher_priority_source(
    db: AsyncSession, equipment_id: int, current_source: str,
) -> str | None:
    """Returns the name of a higher-priority source that has previously
    touched this equipment, or None when ``current_source`` is allowed to
    update freely. Used to enforce P&ID-takes-precedence-over-PFD/Vendor.
    """
    current_p = _SOURCE_PRIORITY.get(current_source, 0)
    # Sources that outrank what's being applied now.
    higher = [s for s, p in _SOURCE_PRIORITY.items() if p > current_p and p < 99]
    if not higher:
        return None
    row = (await db.execute(
        select(EquipmentVersion.source)
        .where(
            EquipmentVersion.equipment_id == equipment_id,
            EquipmentVersion.source.in_(higher),
        )
        .limit(1)
    )).scalar_one_or_none()
    return row

# Fields that participate in equality / versioning. `data` is included separately.
TRACKED_FIELDS = [
    "rev_no", "old_tag", "client_tag", "description", "vendor", "equipment_type",
    "module", "design_code", "orientation", "material", "configuration", "location",
    "operating_press", "operating_temp", "design_press", "design_temp", "design_flow",
    "pump_capacity", "heat_exchanger_duty_kw", "liquid_fill",
    "absorbed_power_kw", "rated_power_kw",
    "length_m", "width_id_m", "height_tt_m",
    "dry_weight_mt", "operating_weight_mt", "hydrotest_weight_mt",
    "pid", "remarks", "total_dry_weight_mt", "total_operating_weight_mt",
    "lifecycle_status",
    # Extra dims / design conditions captured from vendor drawings
    "length_overall_m", "mdmt_c", "hydrostatic_test_press_barg", "insulation",
]


def snapshot(eq: Equipment) -> dict[str, Any]:
    snap = {f: getattr(eq, f) for f in TRACKED_FIELDS}
    snap["data"] = dict(eq.data or {})
    return snap


def _is_strictly_more_informative(old_val: Any, new_val: Any) -> bool:
    """Heuristic: True when the existing value carries strictly more
    information than the incoming value and should be preserved.

    Catches the common engineering-doc pattern where the Master Equipment
    List has a RANGE (e.g. ``"0.1 / 3.5"``, ``"FV / 10"``, ``"13.9 / 60"``,
    ``"-40 / 120"``) and a downstream sync (PFD) tries to overwrite it with
    a single design-point value (``"3.5"``, ``"10"``, ``"60"``, ``"120"``)
    that already lives inside that range. In that case we keep the range
    because dropping the lower bound is data loss.

    Counter-example: ``"0.17"`` vs ``"0.20"`` — the old value isn't a
    range; the new value is genuinely different, so vendor weights still
    overwrite the older estimate normally.
    """
    if not isinstance(old_val, str) or not isinstance(new_val, str):
        return False
    if "/" not in old_val:
        return False
    parts = [p.strip() for p in old_val.split("/")]
    if len(parts) < 2:
        return False
    new_clean = new_val.strip()
    # Match against the literal components AND a whitespace-collapsed form
    # (so e.g. "0.1/3.5" matches a part stored as "0.1" or "3.5").
    if new_clean in parts:
        return True
    # Also handle "(-)30" vs "-30" sign variant inside ranges like "(-)30 / 100".
    new_no_parens = new_clean.replace("(", "").replace(")", "")
    parts_no_parens = [p.replace("(", "").replace(")", "") for p in parts]
    if new_no_parens in parts_no_parens:
        return True
    return False


async def apply_update(
    db: AsyncSession,
    eq: Equipment,
    updates: dict[str, Any],
    *,
    source: str,
    source_file_id: int | None,
    user_id: int | None,
    note: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> EquipmentVersion | None:
    """Apply updates to `eq`. Returns the new version row, or None if nothing changed.

    Semantics: every ``EquipmentVersion`` row stores the snapshot of the
    equipment **AT that version** — i.e. *after* this update's changes are
    applied. This matches what ``record_initial_version`` does for v1 and
    keeps the diff endpoint trivial: comparing v_i to v_j is just a dict
    diff of their two snapshots.

    The caller is responsible for ``db.commit()``.

    Source-precedence rule: if this equipment row has been touched by a
    higher-priority source (per ``_SOURCE_PRIORITY``), we silently skip
    the update. Concretely: once a row has a P&ID version, PFD and
    Vendor syncs are no-ops on that row. ``manual`` and ``excel`` always
    win and can override P&ID-locked rows when the engineer needs to.
    """
    blocked_by = await _has_higher_priority_source(db, eq.id, source)
    if blocked_by:
        # Caller (sync_service) can detect via the None return that this
        # was a precedence skip rather than a real no-op. We don't raise
        # here because a single sync can hit dozens of these and stack
        # traces would be noise.
        return None

    changed: list[str] = []
    preserved: list[str] = []

    for field, value in updates.items():
        if field not in TRACKED_FIELDS:
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        existing = getattr(eq, field)
        if existing == value:
            continue
        # Don't replace a richer range with a single component of itself.
        # Example: keep "0.1 / 3.5" rather than overwriting with "3.5".
        if _is_strictly_more_informative(existing, value):
            preserved.append(field)
            continue
        setattr(eq, field, value)
        changed.append(field)

    if extra_data:
        merged = dict(eq.data or {})
        merged.update(extra_data)
        if merged != (eq.data or {}):
            eq.data = merged
            changed.append("data")

    if not changed:
        return None

    eq.current_version = (eq.current_version or 0) + 1
    eq.last_source = source
    eq.last_source_file_id = source_file_id
    eq.last_updated_by_id = user_id

    # Take the snapshot AFTER applying updates so it reflects the state
    # at this new version.
    version = EquipmentVersion(
        equipment_id=eq.id,
        version_no=eq.current_version,
        snapshot=snapshot(eq),
        changed_fields=changed,
        source=source,
        source_file_id=source_file_id,
        note=note,
        created_by_id=user_id,
    )
    db.add(version)
    await db.flush()
    return version


async def record_initial_version(
    db: AsyncSession,
    eq: Equipment,
    *,
    source: str,
    source_file_id: int | None = None,
    user_id: int | None = None,
) -> EquipmentVersion:
    """Record version 1 for a freshly-created equipment row."""
    eq.current_version = 1
    eq.last_source = source
    eq.last_source_file_id = source_file_id
    eq.last_updated_by_id = user_id
    eq.created_by_id = eq.created_by_id or user_id
    version = EquipmentVersion(
        equipment_id=eq.id,
        version_no=1,
        snapshot=snapshot(eq),
        changed_fields=list(TRACKED_FIELDS),
        source=source,
        source_file_id=source_file_id,
        created_by_id=user_id,
    )
    db.add(version)
    await db.flush()
    return version


def diff_snapshots(a: dict[str, Any], b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields = set(a) | set(b)
    out: dict[str, dict[str, Any]] = {}
    for f in fields:
        if a.get(f) != b.get(f):
            out[f] = {"from": a.get(f), "to": b.get(f)}
    return out
