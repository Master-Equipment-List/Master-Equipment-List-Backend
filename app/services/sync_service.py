"""Sync orchestrator.

1. For each `ProjectOneDriveSelection`, download the file (or walk a folder).
2. Parse with the dispatcher.
3. Persist a `FileExtraction`.
4. Run the domain extractors that match the file's folder category:
    - PFD Samples   -> pfd_extractor -> equipment updates
    - Vendor Data   -> vendor_extractor -> equipment updates per matching tag
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.extractors import pfd_extractor, vendor_extractor
from app.extractors.tags import find_fuzzy_tag_match, normalize_tag
from app.models import (
    Equipment,
    FileExtraction,
    ProjectFile,
    ProjectOneDriveSelection,
    Project,
)
from app.parsers import parse_file
from app.services import audit_service, onedrive_service
from app.services.duplicate_detection import find_duplicate_candidate
from app.services.pending_change_service import queue_pending_change, queue_possible_duplicate


log = logging.getLogger(__name__)


PFD_CATEGORY = "PFD Samples"
PID_CATEGORY = "P&ID"
VENDOR_CATEGORY = "Vendor Data"


def _workspace_onedrive_root_path(project: Project, workspace: str) -> str | None:
    """Resolve which OneDrive root path applies for the given workspace.

    Falls back to the legacy project.onedrive_root_path if the workspace-
    specific column isn't populated yet (eases the transition for
    existing projects).
    """
    if workspace == "marine":
        return project.marine_onedrive_root_path or None
    # Default: topside (or any unknown value)
    return project.topside_onedrive_root_path or project.onedrive_root_path or None


def _category_for_path(project_root: str | None, path: str) -> str | None:
    """Infer folder category from path relative to project root.

    Matches path SEGMENTS (case-insensitive) rather than substring-matching
    the whole path — that way a filename like ``20171-...-PFD-...pdf``
    doesn't accidentally count as a "PFD" folder, and we accept short
    aliases like ``PFD`` (no "Samples"), ``Vendor`` (no "Data"), ``PID``
    (no ampersand) without false positives.
    """
    if not path:
        return None

    # Aliases that should resolve to each canonical category. Edit here if
    # an EPC uses a folder name you'd like to support.
    PFD_ALIASES = {
        "pfd", "pfds",
        "pfd samples", "pfd sample",
        "pfd drawings", "pfd drawing",
        "process flow", "process flow diagram", "process flow diagrams",
    }
    PID_ALIASES = {
        # Ampersand spellings
        "p&id", "p&ids",
        "p&id samples", "p&id sample",       # ← the user's folder name
        "p&id drawings", "p&id drawing",
        "p&id documents",
        # No-ampersand spellings
        "pid", "pids",
        "pid samples", "pid sample",
        "pid drawings",
        "pnid", "pnids",
        # Spelled-out
        "p and id", "p and ids",
        "p and id samples", "p and id drawings",
        "piping and instrumentation", "piping and instrumentation diagram",
        "piping and instrumentation diagrams",
    }
    VENDOR_ALIASES = {
        "vendor data", "vendor data sheets", "vendor data sheet",
        "vendor", "vendors",
        "vendor drawings", "vendor drawing",
        "vendor docs", "vendor documents",
        "vendor ga", "vendor gas",
        "datasheet", "datasheets", "data sheets", "data sheet",
    }

    segments = [s.strip().lower() for s in path.replace("\\", "/").split("/") if s.strip()]
    for seg in segments:
        if seg in PFD_ALIASES:
            return PFD_CATEGORY
        if seg in PID_ALIASES:
            return PID_CATEGORY
        if seg in VENDOR_ALIASES:
            return VENDOR_CATEGORY
    return None


async def _ensure_local_file(
    db: AsyncSession, project: Project, drive_item: dict[str, Any], local_dir: Path
) -> str:
    local_dir.mkdir(parents=True, exist_ok=True)
    safe_name = drive_item["name"].replace("/", "_").replace("\\", "_")
    target = local_dir / f"{drive_item['id']}__{safe_name}"
    await onedrive_service.download_item(db, project, drive_item["id"], str(target))
    return str(target)


async def _expand_selections(
    db: AsyncSession, project: Project, workspace: str = "topside"
) -> list[dict[str, Any]]:
    selections = (
        await db.execute(
            select(ProjectOneDriveSelection).where(
                ProjectOneDriveSelection.project_id == project.id,
                ProjectOneDriveSelection.workspace == workspace,
            )
        )
    ).scalars().all()

    expanded: list[dict[str, Any]] = []
    for sel in selections:
        if sel.item_type == "file":
            expanded.append(
                {
                    "id": sel.item_id,
                    "name": sel.name,
                    "path": sel.item_path,
                    "type": "file",
                    "size": sel.size_bytes,
                }
            )
        else:
            async for item in onedrive_service.walk_folder(db, project, sel.item_id):
                expanded.append(item)
    return expanded


async def _upsert_project_file(
    db: AsyncSession,
    project: Project,
    drive_item: dict[str, Any],
    local_path: str,
    workspace: str = "topside",
) -> ProjectFile:
    existing = (
        await db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.onedrive_item_id == drive_item["id"],
            )
        )
    ).scalar_one_or_none()

    extension = Path(drive_item["name"]).suffix.lower() or None
    root_path = _workspace_onedrive_root_path(project, workspace)
    category = _category_for_path(root_path, drive_item.get("path", ""))
    modified_at = None
    if drive_item.get("modified_at"):
        try:
            modified_at = datetime.fromisoformat(drive_item["modified_at"].replace("Z", "+00:00"))
        except Exception:
            modified_at = None

    if existing:
        existing.name = drive_item["name"]
        existing.workspace = workspace
        existing.onedrive_path = drive_item.get("path") or existing.onedrive_path
        existing.folder_category = category
        existing.mime_type = drive_item.get("mime_type")
        existing.extension = extension
        existing.size_bytes = drive_item.get("size")
        existing.onedrive_modified_at = modified_at
        existing.local_path = local_path
        existing.sync_status = "synced"
        existing.sync_error = None
        existing.last_synced_at = datetime.now(timezone.utc)
        return existing

    pf = ProjectFile(
        project_id=project.id,
        workspace=workspace,
        name=drive_item["name"],
        onedrive_item_id=drive_item["id"],
        onedrive_path=drive_item.get("path") or "",
        folder_category=category,
        mime_type=drive_item.get("mime_type"),
        extension=extension,
        size_bytes=drive_item.get("size"),
        onedrive_modified_at=modified_at,
        local_path=local_path,
        sync_status="synced",
        last_synced_at=datetime.now(timezone.utc),
    )
    db.add(pf)
    await db.flush()
    return pf


async def _parse_and_store(db: AsyncSession, pf: ProjectFile) -> FileExtraction:
    # Run parse_file in a thread — it calls vision_pfd_service.extract()
    # which blocks internally (ThreadPoolExecutor + Claude API calls).
    # Offloading to a thread keeps the asyncio event loop free so other
    # requests continue to be served while vision is running.
    result = await asyncio.to_thread(parse_file, pf.local_path)
    ext = FileExtraction(
        file_id=pf.id,
        parser=result.parser,
        status=result.status,
        error=result.error,
        pages=result.pages,
        used_ocr=result.used_ocr,
        data=result.data,
    )
    db.add(ext)
    await db.flush()
    return ext


async def _apply_pfd_updates(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    extraction: FileExtraction,
    user_id: int | None,
    summary: dict[str, Any] | None = None,
) -> int:
    """Queue PFD updates for admin review on every matching equipment row,
    AND auto-create rows for any tags the PFD reports that don't exist yet
    in the project.

    Reuses the raw vision JSON already on ``extraction.data["vision_pages"]``
    and feeds it to the LLM mapper to pull every equipment tag in the
    header band plus the seven target MEL fields per tag. For each entry:

      * Matching equipment found  → ``queue_pending_change`` (admin reviews
                                    old vs new per field before anything
                                    is written; NOT applied immediately).
      * No match                  → ``create_equipment_from_sync`` (new row +
                                    initial v1 snapshot stamped source="pfd",
                                    still auto-created — nothing existing to
                                    overwrite).

    Returns the number of equipment rows touched (queued for review +
    created). When a ``summary`` dict is passed, it's mutated to record
    creation counts under ``equipment_created`` and queued-review counts
    under ``pending_changes_queued``.
    """
    from app.services import pfd_field_mapper
    from app.services.equipment_create_helper import create_equipment_from_sync

    pages = (extraction.data or {}).get("vision_pages") or []
    if not pages:
        return 0

    # map_pfd_fields makes a synchronous Anthropic API call — run in a thread
    # so the event loop stays responsive during the ~10-20 s round-trip.
    mapping = await asyncio.to_thread(pfd_field_mapper.map_pfd_fields, pages)
    if not mapping:
        return 0

    new_data = dict(extraction.data or {})
    new_data["pfd"] = mapping
    extraction.data = new_data

    equipment_entries = mapping.get("equipment") or []
    if not equipment_entries:
        return 0

    from app.services.version_service import _has_higher_priority_source

    # Preload all equipment once — avoids a full-table scan per tag.
    eq_map = await _load_equipment_map(db, project.id, workspace=file.workspace)

    affected = 0
    for entry in equipment_entries:
        tag = entry.get("client_equipment_tag")
        fields = {k: v for k, v in (entry.get("fields") or {}).items() if v}
        if not tag or not fields:
            continue
        eq = _find_equipment_in_map(eq_map, tag)
        if eq:
            # If this row is locked by a higher-priority source (P&ID),
            # count the skip explicitly so the user sees it in the summary.
            # Don't even queue a proposal for review in that case.
            blocked_by = await _has_higher_priority_source(db, eq.id, "pfd")
            if blocked_by:
                if summary is not None:
                    summary["pid_locked_skips"] = (summary.get("pid_locked_skips") or 0) + 1
                continue
            pc = await queue_pending_change(
                db, eq, fields,
                source="pfd",
                source_file_id=file.id,
                user_id=user_id,
            )
            if pc:
                affected += 1
                if summary is not None:
                    summary["pending_changes_queued"] = (summary.get("pending_changes_queued") or 0) + 1
        else:
            created = await _create_or_flag_duplicate(
                db, project, file, eq_map, tag, fields,
                source="pfd", user_id=user_id, workspace=file.workspace, summary=summary,
            )
            if created:
                affected += 1
    return affected


async def _apply_pid_updates(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    extraction: FileExtraction,
    user_id: int | None,
    summary: dict[str, Any] | None = None,
) -> int:
    """Queue P&ID updates for admin review on every matching equipment row,
    AND auto-create rows for any tags the P&ID names that don't exist yet
    in the project.

    Mirrors ``_apply_pfd_updates``'s shape but stamps source="pid". P&ID
    is the HIGHEST-priority source in the precedence ladder
    (seed < pfd < vendor < pid), so once a row has been touched by a
    P&ID, subsequent PFD/Vendor syncs will be blocked from overwriting
    by ``_has_higher_priority_source`` — which means P&ID itself never
    needs to consult that check.

    Returns the number of equipment rows touched (queued for review +
    created). When a ``summary`` dict is passed, it's mutated to record
    creation counts under ``equipment_created`` and queued-review counts
    under ``pending_changes_queued``.
    """
    from app.services import pid_field_mapper
    from app.services.equipment_create_helper import create_equipment_from_sync

    pages = (extraction.data or {}).get("vision_pages") or []
    if not pages:
        return 0

    # map_pid_fields makes a synchronous Anthropic API call — run in a thread.
    mapping = await asyncio.to_thread(pid_field_mapper.map_pid_fields, pages)
    if not mapping:
        return 0

    new_data = dict(extraction.data or {})
    new_data["pid"] = mapping
    extraction.data = new_data

    equipment_entries = mapping.get("equipment") or []
    if not equipment_entries:
        return 0

    # Preload all equipment once — avoids a full-table scan per tag.
    eq_map = await _load_equipment_map(db, project.id, workspace=file.workspace)

    affected = 0
    for entry in equipment_entries:
        tag = entry.get("client_equipment_tag")
        fields = {k: v for k, v in (entry.get("fields") or {}).items() if v}
        if not tag or not fields:
            continue
        eq = _find_equipment_in_map(eq_map, tag)
        if eq:
            pc = await queue_pending_change(
                db, eq, fields,
                source="pid",
                source_file_id=file.id,
                user_id=user_id,
            )
            if pc:
                affected += 1
                if summary is not None:
                    summary["pending_changes_queued"] = (summary.get("pending_changes_queued") or 0) + 1
        else:
            created = await _create_or_flag_duplicate(
                db, project, file, eq_map, tag, fields,
                source="pid", user_id=user_id, workspace=file.workspace, summary=summary,
            )
            if created:
                affected += 1
    return affected


async def _apply_vendor_updates(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    extraction: FileExtraction,
    user_id: int | None,
    summary: dict[str, Any] | None = None,
) -> int:
    """Queue vendor-data updates for admin review on the matching
    equipment row, OR auto-create the row if no match exists yet.

    The vendor mapper now returns 15 fields per sheet (8 numerics + 7
    context fields like ``description``, ``vendor``, ``material``,
    design conditions). With that richer payload, a vendor PDF alone can
    bootstrap a believable equipment row.
    """
    from app.services import vendor_field_mapper
    from app.services.equipment_create_helper import create_equipment_from_sync

    pages = (extraction.data or {}).get("vision_pages") or []
    if not pages:
        return 0

    # map_vendor_fields makes a synchronous Anthropic API call — run in a thread.
    mapping = await asyncio.to_thread(vendor_field_mapper.map_vendor_fields, pages)
    if not mapping:
        return 0

    new_data = dict(extraction.data or {})
    new_data["vendor"] = mapping
    extraction.data = new_data

    tag = mapping.get("client_equipment_tag")
    raw_fields = mapping.get("fields") or {}
    evidence = mapping.get("evidence") or {}

    # Confidence gate — silently drop every field the mapper marked as
    # "low" confidence. The mapper uses "low" specifically when it found
    # a value but couldn't tie it to an explicit label (e.g. a bare 795
    # in a revision cloud on a heater bundle drawing). We don't want
    # these to auto-apply; we want them to sit in the extraction JSON
    # under `evidence[field].not_found_reason` for reviewer inspection.
    # High + medium extractions apply. NULL values are dropped normally.
    fields: dict[str, Any] = {}
    low_confidence_dropped: list[str] = []
    for k, v in raw_fields.items():
        if not v:
            continue
        conf = ((evidence.get(k) or {}).get("confidence") or "").lower()
        if conf == "low":
            low_confidence_dropped.append(k)
            continue
        fields[k] = v

    if summary is not None and low_confidence_dropped:
        summary["vendor_low_confidence_skips"] = (
            (summary.get("vendor_low_confidence_skips") or 0)
            + len(low_confidence_dropped)
        )

    if not tag or not fields:
        return 0

    from app.services.version_service import _has_higher_priority_source

    eq_map = await _load_equipment_map(db, project.id, workspace=file.workspace)
    eq = _find_equipment_in_map(eq_map, tag)
    if eq:
        blocked_by = await _has_higher_priority_source(db, eq.id, "vendor")
        if blocked_by:
            if summary is not None:
                summary["pid_locked_skips"] = (summary.get("pid_locked_skips") or 0) + 1
            return 0
        pc = await queue_pending_change(
            db, eq, fields,
            source="vendor",
            source_file_id=file.id,
            user_id=user_id,
        )
        if pc and summary is not None:
            summary["pending_changes_queued"] = (summary.get("pending_changes_queued") or 0) + 1
        return 1 if pc else 0
    else:
        created = await _create_or_flag_duplicate(
            db, project, file, eq_map, tag, fields,
            source="vendor", user_id=user_id, workspace=file.workspace, summary=summary,
        )
        return 1 if created else 0


async def _apply_equipment_list_updates(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    user_id: int | None,
    workspace: str,
    summary: dict[str, Any] | None = None,
) -> int:
    """Auto-detect and import an Equipment List Excel file synced via
    OneDrive — no folder-category is required for this, unlike PFD/P&ID/
    Vendor. ANY synced ``.xlsx``/``.xlsm`` is tried through the same
    row-parser the manual "Import Equipment List" page uses
    (``topside_excel.extract_equipment_rows``); if it doesn't find a
    recognizable client-tag column, this quietly returns 0 — most synced
    Excel files are NOT equipment lists (title blocks, calc sheets, etc.),
    and that's expected, not an error.

    Unlike the manual import's "update_existing" mode, an existing tag's
    changes are NOT applied immediately — they're queued via
    ``queue_pending_change`` for admin review (old vs new per field). A
    tag not seen yet still gets created immediately via
    ``create_equipment_from_sync`` — nothing existing to overwrite there.
    """
    from app.extractors.topside_excel import extract_equipment_rows
    from app.services.equipment_create_helper import create_equipment_from_sync
    from app.services.version_service import TRACKED_FIELDS

    try:
        rows = extract_equipment_rows(file.local_path)
    except Exception as e:  # noqa: BLE001
        log.info("Equipment-list auto-import: %s doesn't parse as one (%s)", file.name, e)
        return 0
    if not rows:
        return 0

    eq_map = await _load_equipment_map(db, project.id, workspace=workspace)

    seen_in_file: set[str] = set()
    affected = 0
    for row in rows:
        tag = (row.get("client_tag") or "").strip()
        if not tag:
            continue
        norm = normalize_tag(tag)
        if norm in seen_in_file:
            continue  # duplicate tag within this file — keep the first occurrence
        seen_in_file.add(norm)

        fields = {
            k: v for k, v in row.items()
            if k not in ("client_tag", "__raw") and k in TRACKED_FIELDS and v
        }
        if not fields:
            continue

        eq = _find_equipment_in_map(eq_map, tag)
        if eq:
            pc = await queue_pending_change(
                db, eq, fields,
                source="excel",
                source_file_id=file.id,
                user_id=user_id,
            )
            if pc:
                affected += 1
                if summary is not None:
                    summary["pending_changes_queued"] = (summary.get("pending_changes_queued") or 0) + 1
        else:
            new_eq = await _create_or_flag_duplicate(
                db, project, file, eq_map, tag, fields,
                source="excel", user_id=user_id, workspace=workspace, summary=summary,
            )
            if new_eq:
                eq_map[normalize_tag(tag)] = new_eq
                affected += 1
    return affected


async def _load_equipment_map(
    db: AsyncSession, project_id: int, workspace: str | None = None
) -> dict[str, Equipment]:
    """Load all equipment for a project into a {normalized_tag: Equipment} dict.

    Call once before a batch of tag lookups; pass the result to
    ``_find_equipment_in_map`` instead of hitting the DB per tag.
    """
    stmt = select(Equipment).where(Equipment.project_id == project_id)
    if workspace:
        stmt = stmt.where(Equipment.workspace == workspace)
    rows = (await db.execute(stmt)).scalars().all()
    mapping: dict[str, Equipment] = {}
    for r in rows:
        if r.client_tag:
            mapping[normalize_tag(r.client_tag)] = r
        if r.old_tag:
            mapping.setdefault(normalize_tag(r.old_tag), r)
    return mapping


def _find_equipment_in_map(eq_map: dict[str, Equipment], tag: str) -> Equipment | None:
    eq = eq_map.get(normalize_tag(tag))
    if eq:
        return eq
    # Exact match failed — vision extraction sometimes misreads a single
    # character (e.g. "S" as "5") on small drawing text. Before treating
    # this as a brand-new tag (and creating a duplicate equipment row),
    # check whether it's a one-character-confusable match against a tag
    # that already exists in this project.
    fuzzy_key = find_fuzzy_tag_match(tag, eq_map.keys())
    if fuzzy_key:
        log.info(
            "Tag %r matched existing equipment %r via confusable-character "
            "correction (likely vision misread)", tag, fuzzy_key,
        )
        return eq_map.get(fuzzy_key)
    return None


async def _create_or_flag_duplicate(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    eq_map: dict[str, Equipment],
    tag: str,
    fields: dict[str, Any],
    *,
    source: str,
    user_id: int | None,
    workspace: str,
    summary: dict[str, Any] | None,
) -> Equipment | None:
    """A tag not found by exact/fuzzy TAG match — before blindly creating a
    new equipment row, check whether an EXISTING row's description +
    equipment type fuzzy-match this incoming data closely enough to be the
    same physical equipment under a different/corrected tag (a vision
    misread, a renumbering). If so, queue it for admin review instead of
    silently creating a probable duplicate; the admin decides whether it's
    genuinely new or the same thing under the existing tag.

    Returns the newly-created Equipment, or ``None`` if a duplicate was
    flagged instead (nothing created in that case).
    """
    from app.services.equipment_create_helper import create_equipment_from_sync

    candidate = find_duplicate_candidate(
        eq_map.values(),
        description=fields.get("description"),
        equipment_type=fields.get("equipment_type"),
        incoming_tag=tag,
    )
    if candidate:
        pc = await queue_possible_duplicate(
            db, candidate, tag.strip(), fields,
            source=source, source_file_id=file.id, user_id=user_id,
        )
        if pc and summary is not None:
            summary["possible_duplicates_flagged"] = (
                summary.get("possible_duplicates_flagged") or 0
            ) + 1
        return None

    new_eq = await create_equipment_from_sync(
        db, project.id, tag.strip(), fields,
        source=source, source_file_id=file.id, user_id=user_id, workspace=workspace,
    )
    if summary is not None:
        summary["equipment_created"] = (summary.get("equipment_created") or 0) + 1
    return new_eq


async def _find_equipment_by_tag(
    db: AsyncSession, project_id: int, tag: str, workspace: str | None = None,
) -> Equipment | None:
    """Single-tag lookup. Prefer _load_equipment_map + _find_equipment_in_map
    when doing many lookups in one pass (avoids N full-table scans).
    """
    eq_map = await _load_equipment_map(db, project_id, workspace)
    return _find_equipment_in_map(eq_map, tag)


def _parse_modified(drive_item: dict[str, Any]) -> datetime | None:
    raw = drive_item.get("modified_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


async def _existing_project_file(
    db: AsyncSession, project: Project, drive_item: dict[str, Any]
) -> ProjectFile | None:
    return (
        await db.execute(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.onedrive_item_id == drive_item["id"],
            )
        )
    ).scalar_one_or_none()


async def _process_drive_items(
    db: AsyncSession,
    project: Project,
    drive_items: list[dict[str, Any]],
    local_dir: Path,
    workspace: str,
    user_id: int | None,
    force: bool,
    summary: dict[str, Any],
) -> None:
    """Two-phase pipeline for syncing a list of drive items.

    ── Phase 1 (parallel) ──────────────────────────────────────────────────
    Up to 3 files are downloaded and vision-parsed concurrently. The
    Anthropic SDK is synchronous so each file's parse runs in its own
    thread (asyncio.to_thread inside _parse_and_store). While one file is
    waiting for Claude to respond the event loop can advance other files'
    downloads, token checks, etc.

    ── Phase 2 (sequential) ────────────────────────────────────────────────
    All DB writes and field-mapper LLM calls happen in order, one file at a
    time. This keeps the SQLAlchemy AsyncSession safe (it is not designed
    for concurrent coroutine use) and prevents conflicting writes to the
    same equipment row when multiple files refer to the same tag.
    """

    # ── Phase 1: parallel skip-check + download + vision ──────────────────
    sem = asyncio.Semaphore(3)  # max 3 files in-flight simultaneously

    async def _fetch_one(it: dict[str, Any]) -> dict[str, Any]:
        """Download + vision-parse one item. No equipment DB writes."""
        async with sem:
            existing = await _existing_project_file(db, project, it)
            incoming_modified = _parse_modified(it)

            if (
                not force
                and existing
                and existing.sync_status == "synced"
                and existing.onedrive_modified_at is not None
                and incoming_modified is not None
                and existing.onedrive_modified_at == incoming_modified
                and existing.local_path
                and Path(existing.local_path).exists()
            ):
                # Touch last_synced_at so we know this was checked.
                existing.last_synced_at = datetime.now(timezone.utc)
                return {"action": "skip", "it": it}

            local_path = await _ensure_local_file(db, project, it, local_dir)
            # parse_file calls vision_pfd_service.extract() which itself fans
            # out to many Claude API calls via a ThreadPoolExecutor. Running
            # the whole thing in a thread lets other files proceed in the event
            # loop while this file's vision is in-flight.
            parse_result = await asyncio.to_thread(parse_file, local_path)
            return {
                "action": "process",
                "it": it,
                "local_path": local_path,
                "parse_result": parse_result,
            }

    fetch_results = await asyncio.gather(
        *[_fetch_one(it) for it in drive_items],
        return_exceptions=True,
    )

    # ── Phase 2: sequential DB writes ─────────────────────────────────────
    for fetch in fetch_results:
        if isinstance(fetch, Exception):
            summary["files_failed"] += 1
            summary["errors"].append({"item": "unknown", "error": str(fetch)})
            continue

        it: dict[str, Any] = fetch["it"]
        try:
            if fetch["action"] == "skip":
                summary["files_skipped"] += 1
                continue

            local_path: str = fetch["local_path"]
            parse_result = fetch["parse_result"]

            pf = await _upsert_project_file(
                db, project, it, local_path, workspace=workspace
            )

            # Persist the pre-computed ParseResult directly (no re-parsing).
            ext = FileExtraction(
                file_id=pf.id,
                parser=parse_result.parser,
                status=parse_result.status,
                error=parse_result.error,
                pages=parse_result.pages,
                used_ocr=parse_result.used_ocr,
                data=parse_result.data,
            )
            db.add(ext)
            await db.flush()

            if pf.folder_category == PFD_CATEGORY:
                summary["pfd_updates_applied"] += await _apply_pfd_updates(
                    db, project, pf, ext, user_id, summary
                )
            elif pf.folder_category == PID_CATEGORY:
                summary["pid_updates_applied"] += await _apply_pid_updates(
                    db, project, pf, ext, user_id, summary
                )
            elif pf.folder_category == VENDOR_CATEGORY:
                summary["vendor_updates_applied"] += await _apply_vendor_updates(
                    db, project, pf, ext, user_id, summary
                )

            # Equipment List auto-detect is folder-independent — try it for
            # every synced Excel file regardless of category (unlike
            # PFD/P&ID/Vendor above, which are gated on folder placement).
            if pf.extension in (".xlsx", ".xlsm"):
                summary["equipment_list_updates_applied"] = (
                    summary.get("equipment_list_updates_applied") or 0
                ) + await _apply_equipment_list_updates(
                    db, project, pf, user_id, workspace, summary
                )

            summary["files_synced"] += 1

        except Exception as e:  # noqa: BLE001
            summary["files_failed"] += 1
            summary["errors"].append({"item": it.get("name"), "error": str(e)})


async def run_sync(
    db: AsyncSession,
    project: Project,
    user_id: int | None = None,
    *,
    force: bool = False,
    workspace: str = "topside",
) -> dict[str, Any]:
    """Top-level sync runner for one workspace.

    Only selections / files attached to this workspace are processed.
    The OneDrive root used for browsing is the workspace-specific one
    on the project row.
    """
    local_dir = settings.storage_path / f"project_{project.id}_{workspace}"
    summary = {
        "project_id": project.id,
        "workspace": workspace,
        "force": force,
        "files_synced": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "pfd_updates_applied": 0,
        "pid_updates_applied": 0,
        "vendor_updates_applied": 0,
        "equipment_list_updates_applied": 0,
        # Existing-row updates from PFD/P&ID/Vendor/Excel are no longer
        # applied immediately — they're queued here for admin review
        # (see EquipmentPendingChange). New equipment (tags not seen
        # before) still auto-creates and is NOT part of this count.
        "pending_changes_queued": 0,
        # A tag that didn't match anything, but whose description +
        # equipment type fuzzy-matched an EXISTING row under a different
        # tag — queued for admin review (confirm as new vs merge into the
        # match) instead of silently auto-creating a probable duplicate.
        "possible_duplicates_flagged": 0,
        "equipment_created": 0,
        # Updates that were silently skipped because a higher-priority
        # source (P&ID) had already set the row. Helps users understand
        # why PFD/Vendor updates didn't take effect this sync.
        "pid_locked_skips": 0,
        # Fields the vendor mapper flagged as "low confidence" (couldn't
        # tie the value to an explicit label) — held back from auto-apply
        # so the reviewer sees the not_found_reason in the extraction
        # JSON and can decide whether to enter the value manually.
        "vendor_low_confidence_skips": 0,
        "errors": [],
    }

    drive_items = await _expand_selections(db, project, workspace=workspace)

    await _process_drive_items(
        db, project, drive_items, local_dir, workspace, user_id, force, summary
    )

    await audit_service.log(
        db,
        action="project.sync",
        user_id=user_id,
        project_id=project.id,
        metadata=summary,
    )
    await db.commit()
    return summary


async def sync_single_item(
    db: AsyncSession,
    project: Project,
    item_id: str,
    user_id: int | None = None,
    *,
    force: bool = False,
    workspace: str = "topside",
) -> dict[str, Any]:
    """Sync ONE OneDrive item by its drive-item id, without touching the
    project's persistent selection.

    If the item is a folder, walks it recursively and syncs every file inside.
    Useful for ad-hoc "sync this file" / "sync this folder" actions from the
    browse list.
    """
    local_dir = settings.storage_path / f"project_{project.id}_{workspace}"
    summary: dict[str, Any] = {
        "project_id": project.id,
        "workspace": workspace,
        "force": force,
        "item_id": item_id,
        "files_synced": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "pfd_updates_applied": 0,
        "pid_updates_applied": 0,
        "vendor_updates_applied": 0,
        "equipment_list_updates_applied": 0,
        # Existing-row updates from PFD/P&ID/Vendor/Excel are no longer
        # applied immediately — they're queued here for admin review
        # (see EquipmentPendingChange). New equipment (tags not seen
        # before) still auto-creates and is NOT part of this count.
        "pending_changes_queued": 0,
        # A tag that didn't match anything, but whose description +
        # equipment type fuzzy-matched an EXISTING row under a different
        # tag — queued for admin review (confirm as new vs merge into the
        # match) instead of silently auto-creating a probable duplicate.
        "possible_duplicates_flagged": 0,
        "equipment_created": 0,
        # Updates that were silently skipped because a higher-priority
        # source (P&ID) had already set the row. Helps users understand
        # why PFD/Vendor updates didn't take effect this sync.
        "pid_locked_skips": 0,
        # Fields the vendor mapper flagged as "low confidence" (couldn't
        # tie the value to an explicit label) — held back from auto-apply
        # so the reviewer sees the not_found_reason in the extraction
        # JSON and can decide whether to enter the value manually.
        "vendor_low_confidence_skips": 0,
        "errors": [],
    }

    # Resolve the item via Graph
    try:
        item = await onedrive_service.get_item(db, project, item_id)
    except onedrive_service.OneDriveError as e:
        raise RuntimeError(f"Could not resolve OneDrive item {item_id}: {e}")

    is_folder = "folder" in item
    name = item.get("name") or "(unknown)"

    if is_folder:
        drive_items: list[dict[str, Any]] = []
        async for child in onedrive_service.walk_folder(db, project, item_id):
            drive_items.append(child)
    else:
        # Single file — normalize into the same shape as walk_folder yields
        drive_items = [{
            "id": item["id"],
            "name": item.get("name"),
            "path": (item.get("parentReference", {}).get("path", "") + "/" + item.get("name", "")).replace("//", "/"),
            "type": "file",
            "size": item.get("size"),
            "modified_at": item.get("lastModifiedDateTime"),
            "mime_type": (item.get("file") or {}).get("mimeType"),
        }]

    await _process_drive_items(
        db, project, drive_items, local_dir, workspace, user_id, force, summary
    )

    await audit_service.log(
        db,
        action="project.sync_item",
        user_id=user_id,
        project_id=project.id,
        metadata={"item_id": item_id, "item_name": name, **summary},
    )
    await db.commit()
    return summary
