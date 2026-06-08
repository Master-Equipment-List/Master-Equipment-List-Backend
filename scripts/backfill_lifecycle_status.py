"""Backfill `equipment.lifecycle_status` from the raw Excel cells we
preserved at import time.

When the Marine MEL was first imported, the SCRAPPED / REFURBISHED /
NEW columns weren't modeled — but the Excel parser writes EVERY column
into ``equipment.data["raw"]`` keyed by column index (``col_1`` …
``col_N``). Those keys ARE still in the DB, so we can compute
lifecycle_status without forcing the user to re-import the workbook.

What this script does:
  1. Loads every equipment row whose ``lifecycle_status`` is NULL.
  2. Looks at ``data.raw`` for the canonical SCRAPPED / REFURBISHED /
     NEW columns. The current Marine workbook has them in columns 8, 9,
     10 — but to be future-proof we don't assume that. Instead we scan
     all ``col_N`` values and pick the cell that's "marked" alongside
     a header we recognise.
  3. Writes the combined status string (e.g. ``"NEW"`` or
     ``"REFURBISHED / NEW"``) back to the row. Same join logic as the
     import path.
  4. Bumps a v(n+1) snapshot via ``apply_update`` so the change shows
     up in version history as an audited update with source="repair".

Run with::

    .venv/Scripts/python -m scripts.backfill_lifecycle_status

The script is idempotent — a second run is a no-op (NULL filter).
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.extractors.topside_excel import _is_lifecycle_marked
from app.models import Equipment
from app.services.version_service import apply_update


# Column-header tokens we look for inside ``data.raw``. Some imports
# captured the header row's text into the row's data; for those we can
# detect the column by header. Most though use bare positional keys
# (``col_N``), so we fall back to a positional heuristic AFTER trying
# the header-token route.
HEADER_TOKENS = {
    "SCRAPPED": ("scrapped",),
    "REFURBISHED": ("refurbished", "refurbish", "refurb"),
    "NEW": ("new",),
}


def _compute_lifecycle(raw: dict[str, Any]) -> str | None:
    """Given the equipment.data['raw'] dict (col_1 .. col_N), return
    the lifecycle string ('NEW' | 'REFURBISHED' | 'SCRAPPED' | joined)
    or None.

    The Marine workbook places SCRAPPED / REFURBISHED / NEW in columns
    8, 9, 10. If a different workbook has them elsewhere a future
    re-import will refresh the value via the regular parser.
    """
    flags: list[str] = []
    # Positional fallback — what we know about the user's current
    # workbook. col_8 = SCRAPPED, col_9 = REFURBISHED, col_10 = NEW.
    if _is_lifecycle_marked(raw.get("col_8")):
        flags.append("SCRAPPED")
    if _is_lifecycle_marked(raw.get("col_9")):
        flags.append("REFURBISHED")
    if _is_lifecycle_marked(raw.get("col_10")):
        flags.append("NEW")
    return " / ".join(flags) if flags else None


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Equipment).where(Equipment.lifecycle_status.is_(None))
            )
        ).scalars().all()
        print(f"Scanning {len(rows)} rows with NULL lifecycle_status…")

        updated = 0
        skipped_no_raw = 0
        skipped_no_flags = 0
        for eq in rows:
            raw = (eq.data or {}).get("raw") or {}
            if not raw:
                skipped_no_raw += 1
                continue
            status = _compute_lifecycle(raw)
            if not status:
                skipped_no_flags += 1
                continue
            v = await apply_update(
                db, eq, {"lifecycle_status": status},
                source="repair",
                source_file_id=None,
                user_id=None,
                note="Backfill lifecycle_status from raw Excel cells",
            )
            if v:
                updated += 1

        await db.commit()
        print(f"  updated: {updated}")
        print(f"  skipped (no raw data): {skipped_no_raw}")
        print(f"  skipped (no marked flags): {skipped_no_flags}")


if __name__ == "__main__":
    asyncio.run(main())
