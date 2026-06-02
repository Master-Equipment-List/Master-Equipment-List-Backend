"""One-shot backfill: fix EquipmentVersion.snapshot for rows created under
the old (buggy) semantics where ``apply_update`` stored the PRIOR state
on the version row instead of the state AT that version.

Old behaviour for an equipment with versions v1, v2, v3:
    v1.snapshot = state at v1                       (correct — from record_initial_version)
    v2.snapshot = state at v1  (was "prior" of v2)  ✗ wrong
    v3.snapshot = state at v2  (was "prior" of v3)  ✗ wrong
    current equipment row = state at v3

After this script:
    v1.snapshot = state at v1                       (unchanged)
    v2.snapshot = state at v2 = previous v3.snapshot
    v3.snapshot = state at v3 = snapshot(current equipment row)

The script is idempotent for already-correct data: it only rewrites rows
where the old snapshot differs from the computed correct snapshot.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import Equipment, EquipmentVersion
from app.services.version_service import snapshot


async def main() -> None:
    rewritten = 0
    skipped_correct = 0
    equipments_touched = 0

    async with AsyncSessionLocal() as db:
        equipments = (await db.execute(select(Equipment))).scalars().all()
        print(f"Scanning {len(equipments)} equipment rows…")

        for eq in equipments:
            versions = (
                await db.execute(
                    select(EquipmentVersion)
                    .where(EquipmentVersion.equipment_id == eq.id)
                    .order_by(EquipmentVersion.version_no.asc())
                )
            ).scalars().all()
            if len(versions) < 2:
                # v1 already stores the correct snapshot — nothing to do.
                continue

            # Compute what each version's snapshot SHOULD be under the new
            # forward-looking semantics:
            #   v1            → keep its current snapshot
            #   v_i, 1<i<last → next version's OLD snapshot (which was v_i's true state)
            #   v_last        → snapshot of the live equipment row
            new_snapshots: dict[int, dict] = {}
            for i, v in enumerate(versions):
                if i == 0:
                    new_snapshots[v.id] = v.snapshot
                elif i == len(versions) - 1:
                    new_snapshots[v.id] = snapshot(eq)
                else:
                    new_snapshots[v.id] = versions[i + 1].snapshot

            touched_this_eq = False
            for v in versions:
                target = new_snapshots[v.id]
                if v.snapshot == target:
                    skipped_correct += 1
                    continue
                v.snapshot = target
                rewritten += 1
                touched_this_eq = True

            if touched_this_eq:
                equipments_touched += 1

        await db.commit()

    print()
    print(f"Equipment rows touched : {equipments_touched}")
    print(f"Version rows rewritten : {rewritten}")
    print(f"Version rows unchanged : {skipped_correct}")
    print()
    print("Done. The Compare versions diff should now match the change list "
          "shown in Version history.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
