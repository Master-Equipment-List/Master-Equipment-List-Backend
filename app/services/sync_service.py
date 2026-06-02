"""Sync orchestrator.

1. For each `ProjectOneDriveSelection`, download the file (or walk a folder).
2. Parse with the dispatcher.
3. Persist a `FileExtraction`.
4. Run the domain extractors that match the file's folder category:
    - PFD Samples   -> pfd_extractor -> equipment updates
    - Vendor Data   -> vendor_extractor -> equipment updates per matching tag
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.extractors import pfd_extractor, vendor_extractor
from app.extractors.tags import normalize_tag
from app.models import (
    Equipment,
    FileExtraction,
    ProjectFile,
    ProjectOneDriveSelection,
    Project,
)
from app.parsers import parse_file
from app.services import audit_service, onedrive_service
from app.services.version_service import apply_update


PFD_CATEGORY = "PFD Samples"
VENDOR_CATEGORY = "Vendor Data"


def _category_for_path(project_root: str | None, path: str) -> str | None:
    """Infer folder category from path relative to project root."""
    if not path:
        return None
    p = path.replace("\\", "/")
    if PFD_CATEGORY.lower() in p.lower():
        return PFD_CATEGORY
    if VENDOR_CATEGORY.lower() in p.lower():
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
    db: AsyncSession, project: Project
) -> list[dict[str, Any]]:
    selections = (
        await db.execute(
            select(ProjectOneDriveSelection).where(
                ProjectOneDriveSelection.project_id == project.id
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
    db: AsyncSession, project: Project, drive_item: dict[str, Any], local_path: str
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
    category = _category_for_path(project.onedrive_root_path, drive_item.get("path", ""))
    modified_at = None
    if drive_item.get("modified_at"):
        try:
            modified_at = datetime.fromisoformat(drive_item["modified_at"].replace("Z", "+00:00"))
        except Exception:
            modified_at = None

    if existing:
        existing.name = drive_item["name"]
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
    # Vision handles every PDF type (text-based, scanned, drawings)
    # uniformly. force_ocr only matters if vision is unavailable — in
    # that case let the parser decide via its own scanned-page detection.
    result = parse_file(pf.local_path)
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
    """Apply PFD updates to every matching equipment row, AND auto-create
    rows for any tags the PFD reports that don't exist yet in the project.

    Reuses the raw vision JSON already on ``extraction.data["vision_pages"]``
    and feeds it to the LLM mapper to pull every equipment tag in the
    header band plus the seven target MEL fields per tag. For each entry:

      * Matching equipment found  → ``apply_update`` (new version snapshot).
      * No match                  → ``create_equipment_from_sync`` (new row +
                                    initial v1 snapshot stamped source="pfd").

    Returns the number of equipment rows touched (updated + created). When
    a ``summary`` dict is passed, it's mutated to record creation counts
    separately under ``equipment_created`` for visibility in the response.
    """
    from app.services import pfd_field_mapper
    from app.services.equipment_create_helper import create_equipment_from_sync

    pages = (extraction.data or {}).get("vision_pages") or []
    if not pages:
        return 0

    mapping = pfd_field_mapper.map_pfd_fields(pages)
    if not mapping:
        return 0

    new_data = dict(extraction.data or {})
    new_data["pfd"] = mapping
    extraction.data = new_data

    equipment_entries = mapping.get("equipment") or []
    if not equipment_entries:
        return 0

    affected = 0
    for entry in equipment_entries:
        tag = entry.get("client_equipment_tag")
        fields = {k: v for k, v in (entry.get("fields") or {}).items() if v}
        if not tag or not fields:
            continue
        eq = await _find_equipment_by_tag(db, project.id, tag)
        if eq:
            v = await apply_update(
                db, eq, fields,
                source="pfd",
                source_file_id=file.id,
                user_id=user_id,
                note=f"PFD update from {file.name}",
            )
            if v:
                affected += 1
        else:
            await create_equipment_from_sync(
                db, project.id, tag.strip(), fields,
                source="pfd",
                source_file_id=file.id,
                user_id=user_id,
            )
            affected += 1
            if summary is not None:
                summary["equipment_created"] = (summary.get("equipment_created") or 0) + 1
    return affected


async def _apply_vendor_updates(
    db: AsyncSession,
    project: Project,
    file: ProjectFile,
    extraction: FileExtraction,
    user_id: int | None,
    summary: dict[str, Any] | None = None,
) -> int:
    """Apply vendor-data updates to the matching equipment row, OR
    auto-create the row if no match exists yet.

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

    mapping = vendor_field_mapper.map_vendor_fields(pages)
    if not mapping:
        return 0

    new_data = dict(extraction.data or {})
    new_data["vendor"] = mapping
    extraction.data = new_data

    tag = mapping.get("client_equipment_tag")
    fields = {k: v for k, v in (mapping.get("fields") or {}).items() if v}
    if not tag or not fields:
        return 0

    eq = await _find_equipment_by_tag(db, project.id, tag)
    if eq:
        v = await apply_update(
            db, eq, fields,
            source="vendor",
            source_file_id=file.id,
            user_id=user_id,
            note=f"Vendor data update from {file.name}",
        )
        return 1 if v else 0
    else:
        await create_equipment_from_sync(
            db, project.id, tag.strip(), fields,
            source="vendor",
            source_file_id=file.id,
            user_id=user_id,
        )
        if summary is not None:
            summary["equipment_created"] = (summary.get("equipment_created") or 0) + 1
        return 1


async def _find_equipment_by_tag(
    db: AsyncSession, project_id: int, tag: str
) -> Equipment | None:
    norm = normalize_tag(tag)
    rows = (
        await db.execute(select(Equipment).where(Equipment.project_id == project_id))
    ).scalars().all()
    for r in rows:
        if normalize_tag(r.client_tag) == norm:
            return r
        if r.old_tag and normalize_tag(r.old_tag) == norm:
            return r
    return None


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


async def run_sync(
    db: AsyncSession,
    project: Project,
    user_id: int | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Top-level sync runner.

    By default, files whose OneDrive `lastModifiedDateTime` matches what we
    already have on the corresponding ProjectFile row are **skipped** — no
    download, no re-parse, no re-extract. Pass `force=True` to bypass this
    short-circuit and re-process every selected item (useful if the parser
    or extractor logic itself has changed).
    """
    local_dir = settings.storage_path / f"project_{project.id}"
    summary = {
        "project_id": project.id,
        "force": force,
        "files_synced": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "pfd_updates_applied": 0,
        "vendor_updates_applied": 0,
        "equipment_created": 0,
        "errors": [],
    }

    drive_items = await _expand_selections(db, project)

    for it in drive_items:
        try:
            existing = await _existing_project_file(db, project, it)
            incoming_modified = _parse_modified(it)

            # Skip if we already synced this version of the file.
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
                summary["files_skipped"] += 1
                # refresh the last_synced_at marker so we know we "checked"
                existing.last_synced_at = datetime.now(timezone.utc)
                continue

            local_path = await _ensure_local_file(db, project, it, local_dir)
            pf = await _upsert_project_file(db, project, it, local_path)
            extraction = await _parse_and_store(db, pf)

            if pf.folder_category == PFD_CATEGORY:
                summary["pfd_updates_applied"] += await _apply_pfd_updates(
                    db, project, pf, extraction, user_id, summary
                )
            elif pf.folder_category == VENDOR_CATEGORY:
                summary["vendor_updates_applied"] += await _apply_vendor_updates(
                    db, project, pf, extraction, user_id, summary
                )

            summary["files_synced"] += 1
        except Exception as e:  # noqa: BLE001
            summary["files_failed"] += 1
            summary["errors"].append({"item": it.get("name"), "error": str(e)})

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
) -> dict[str, Any]:
    """Sync ONE OneDrive item by its drive-item id, without touching the
    project's persistent selection.

    If the item is a folder, walks it recursively and syncs every file inside.
    Useful for ad-hoc "sync this file" / "sync this folder" actions from the
    browse list.
    """
    local_dir = settings.storage_path / f"project_{project.id}"
    summary: dict[str, Any] = {
        "project_id": project.id,
        "force": force,
        "item_id": item_id,
        "files_synced": 0,
        "files_skipped": 0,
        "files_failed": 0,
        "pfd_updates_applied": 0,
        "vendor_updates_applied": 0,
        "equipment_created": 0,
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

    for it in drive_items:
        try:
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
                summary["files_skipped"] += 1
                existing.last_synced_at = datetime.now(timezone.utc)
                continue

            local_path = await _ensure_local_file(db, project, it, local_dir)
            pf = await _upsert_project_file(db, project, it, local_path)
            extraction = await _parse_and_store(db, pf)

            if pf.folder_category == PFD_CATEGORY:
                summary["pfd_updates_applied"] += await _apply_pfd_updates(
                    db, project, pf, extraction, user_id, summary
                )
            elif pf.folder_category == VENDOR_CATEGORY:
                summary["vendor_updates_applied"] += await _apply_vendor_updates(
                    db, project, pf, extraction, user_id, summary
                )

            summary["files_synced"] += 1
        except Exception as e:  # noqa: BLE001
            summary["files_failed"] += 1
            summary["errors"].append({"item": it.get("name"), "error": str(e)})

    await audit_service.log(
        db,
        action="project.sync_item",
        user_id=user_id,
        project_id=project.id,
        metadata={"item_id": item_id, "item_name": name, **summary},
    )
    await db.commit()
    return summary
