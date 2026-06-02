"""One-shot repair: restore range values that earlier PFD/vendor syncs
clobbered with one of their own components.

Example (H-S67110 from the screenshot):
    Excel imported as v1   → operating_press = "0.1 / 3.5"
    PFD sync ran (v2)      → operating_press = "3.5"     ← single component
    Vendor sync ran (v3)   → operating_press still "3.5" (vendor doesn't say)

After this script:
    operating_press restored to "0.1 / 3.5" (the v1 envelope is preserved).
    The current equipment.current_version is bumped by 1, and a new
    EquipmentVersion row is recorded with source="repair" so the audit
    trail is complete.

The script is idempotent — running it twice yields zero further changes
once everything is restored, because the heuristic only triggers when a
strictly-richer earlier value exists.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import Equipment, EquipmentVersion
from app.services.version_service import (
    TRACKED_FIELDS,
    _is_strictly_more_informative,
    snapshot,
)


async def main() -> None:
    repaired_rows = 0
    repaired_fields = 0
    field_examples: dict[str, list[str]] = {}

    async with AsyncSessionLocal() as db:
        equipments = (await db.execute(select(Equipment))).scalars().all()
        print(f"Scanning {len(equipments)} equipment rows…")

        for eq in equipments:
            # Need at least v1 + one later version to have something to compare.
            versions = (
                await db.execute(
                    select(EquipmentVersion)
                    .where(EquipmentVersion.equipment_id == eq.id)
                    .order_by(EquipmentVersion.version_no.asc())
                )
            ).scalars().all()
            if len(versions) < 2:
                continue

            # Find every field where AN earlier version's value was richer
            # than the current value. We scan ALL earlier versions (not just
            # v1) to catch chained truncations.
            restores: dict[str, Any] = {}
            for field in TRACKED_FIELDS:
                current_val = getattr(eq, field)
                # Walk earlier versions newest→oldest so the LAST richer
                # value seen is preserved (closest to current).
                for v in reversed(versions[:-1]):  # exclude the "current" snapshot
                    earlier = v.snapshot.get(field) if isinstance(v.snapshot, dict) else None
                    if _is_strictly_more_informative(earlier, current_val):
                        restores[field] = earlier
                        break

            if not restores:
                continue

            # Apply the restores
            for field, val in restores.items():
                setattr(eq, field, val)
                repaired_fields += 1
                ex = field_examples.setdefault(field, [])
                if len(ex) < 3:
                    ex.append(f"{eq.client_tag}: → {val!r}")

            # Bump version + record a repair snapshot
            eq.current_version = (eq.current_version or 0) + 1
            eq.last_source = "repair"
            v_new = EquipmentVersion(
                equipment_id=eq.id,
                version_no=eq.current_version,
                snapshot=snapshot(eq),
                changed_fields=list(restores.keys()),
                source="repair",
                source_file_id=None,
                note="Restored range values clobbered by single-component overwrites",
                created_by_id=None,
            )
            db.add(v_new)
            repaired_rows += 1

        await db.commit()

    print()
    print(f"Equipment rows repaired : {repaired_rows}")
    print(f"Fields restored         : {repaired_fields}")
    if field_examples:
        print()
        print("Sample restores by field:")
        for field, examples in sorted(field_examples.items()):
            print(f"  {field}:")
            for e in examples:
                print(f"    {e}")
    print()
    print(f"Finished at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
