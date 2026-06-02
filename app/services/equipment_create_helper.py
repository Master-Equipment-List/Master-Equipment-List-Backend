"""Helper for creating an Equipment row mid-sync.

Used by the sync service when a PFD/Vendor mapper reports a tag that doesn't
exist in the project's equipment table yet. The new row is stamped with the
source that created it (``"pfd"`` or ``"vendor"``) and a v1 ``EquipmentVersion``
snapshot is recorded immediately — so the audit trail and Compare-versions
flow keep working identically to rows created any other way.

Equipment type is best-effort-inferred from the tag prefix (common offshore
oil-and-gas conventions). Module is left null on creation — it's not
reliably extractable from PFD/Vendor JSON, and the user can fill it in
manually from the equipment grid later.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Equipment
from app.services.version_service import TRACKED_FIELDS, record_initial_version


# Tag prefix → equipment type guess. Conservative — common conventions only.
# If a project uses different conventions, this just returns the wrong guess
# the user can correct in the equipment detail page.
_TYPE_BY_PREFIX: dict[str, str] = {
    "V":  "Vessel",
    "D":  "Drum",
    "T":  "Tank",
    "C":  "Column",
    "P":  "Pump",
    "K":  "Compressor",
    "E":  "Heat Exchanger",
    "H":  "Electric Heater",
    "F":  "Filter",
    "A":  "Package",
    "M":  "Mixer",
    "R":  "Reactor",
    "S":  "Strainer",
}


def infer_equipment_type(client_tag: str | None) -> str | None:
    """Best-effort equipment type from the tag prefix.

    Examples::
        "V-S68105"     → "Vessel"
        "P-S37115A/B"  → "Pump"
        "H-S67110"     → "Electric Heater"
        "A-S75130"     → "Package"
        "X-FOO"        → None  (unknown prefix)
    """
    if not client_tag:
        return None
    m = re.match(r"^\s*([A-Z]+)", client_tag.upper())
    if not m:
        return None
    return _TYPE_BY_PREFIX.get(m.group(1))


async def create_equipment_from_sync(
    db: AsyncSession,
    project_id: int,
    client_tag: str,
    fields: dict[str, Any],
    *,
    source: str,
    source_file_id: int | None,
    user_id: int | None,
) -> Equipment:
    """Create an Equipment row + record its initial version snapshot.

    Only fields in ``TRACKED_FIELDS`` are written from the ``fields`` dict —
    everything else is silently dropped. The new row's ``client_tag`` is
    set explicitly (it's a required column), and ``equipment_type`` is
    inferred from the tag prefix if the caller didn't supply one.

    Caller is responsible for ``db.commit()``.
    """
    eq = Equipment(
        project_id=project_id,
        client_tag=client_tag,
    )

    for key, value in (fields or {}).items():
        if key == "client_tag":  # already set
            continue
        if key not in TRACKED_FIELDS:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        setattr(eq, key, value)

    # Best-effort type inference if the mapper didn't return one
    if not eq.equipment_type:
        guessed = infer_equipment_type(client_tag)
        if guessed:
            eq.equipment_type = guessed

    db.add(eq)
    await db.flush()

    await record_initial_version(
        db, eq,
        source=source,
        source_file_id=source_file_id,
        user_id=user_id,
    )
    return eq
