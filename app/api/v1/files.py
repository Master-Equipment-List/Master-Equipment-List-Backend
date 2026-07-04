from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import CurrentUser, DbSession, project_access
from app.models import FileExtraction, Project, ProjectFile
from app.schemas.file import ExtractionOut, FileOut, FileWithExtractionOut
from app.services import audit_service

router = APIRouter()


@router.get("/projects/{project_id}/files", response_model=list[FileOut])
async def list_files(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    category: str | None = Query(None, description="Filter by folder category (e.g. 'PFD Samples')."),
    extension: str | None = Query(None, description="Filter by file extension, e.g. '.pdf'."),
    workspace: str | None = Query(None, description="Filter by workspace: 'topside' or 'marine'."),
):
    stmt = select(ProjectFile).where(ProjectFile.project_id == project.id)
    if workspace:
        stmt = stmt.where(ProjectFile.workspace == workspace)
    if category:
        stmt = stmt.where(ProjectFile.folder_category == category)
    if extension:
        stmt = stmt.where(ProjectFile.extension == extension.lower())
    stmt = stmt.order_by(ProjectFile.name)
    return (await db.execute(stmt)).scalars().all()


@router.get("/projects/{project_id}/files/{file_id}", response_model=FileWithExtractionOut)
async def get_file(
    file_id: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    pf = (
        await db.execute(
            select(ProjectFile)
            .options(selectinload(ProjectFile.extractions))
            .where(ProjectFile.id == file_id, ProjectFile.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not pf:
        raise HTTPException(status_code=404, detail="File not found")
    return pf


@router.get("/projects/{project_id}/files/{file_id}/data", response_model=ExtractionOut)
async def get_file_extraction(
    file_id: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    ext = (
        await db.execute(
            select(FileExtraction)
            .join(ProjectFile, FileExtraction.file_id == ProjectFile.id)
            .where(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project.id,
            )
            .order_by(FileExtraction.id.desc())
        )
    ).scalars().first()
    if not ext:
        raise HTTPException(status_code=404, detail="No extraction available")
    return ext


class ApplyVendorRequest(BaseModel):
    """Apply the vendor fields extracted from a file to either an EXISTING
    equipment row (pass ``equipment_id``) or a NEW one to be created
    (pass ``new_client_tag``). Exactly one of the two should be set.
    """
    equipment_id: int | None = None
    new_client_tag: str | None = None


@router.post("/projects/{project_id}/files/{file_id}/apply-vendor")
async def apply_vendor_to_equipment(
    file_id: int,
    payload: ApplyVendorRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    """Manual fallback for when a vendor sheet's mapper couldn't find a
    client tag. The caller picks (or creates) the equipment row to apply
    the previously-extracted vendor fields to. Goes through the normal
    ``apply_update`` / ``create_equipment_from_sync`` helpers, so a
    proper version snapshot is recorded.
    """
    from app.models import Equipment, FileExtraction
    from app.services.equipment_create_helper import create_equipment_from_sync
    from app.services.version_service import apply_update

    pf = (
        await db.execute(
            select(ProjectFile)
            .options(selectinload(ProjectFile.extractions))
            .where(ProjectFile.id == file_id, ProjectFile.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not pf:
        raise HTTPException(status_code=404, detail="File not found")

    ext = pf.extractions[-1] if pf.extractions else None
    if not ext:
        raise HTTPException(status_code=400, detail="No extraction on this file yet — sync it first.")

    vendor_block = (ext.data or {}).get("vendor") or {}
    fields_raw = vendor_block.get("fields") or {}
    fields = {k: v for k, v in fields_raw.items() if v}
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="No vendor fields were extracted from this file. Nothing to apply.",
        )

    # Decide target equipment.
    if payload.equipment_id is not None:
        eq = (
            await db.execute(
                select(Equipment).where(
                    Equipment.id == payload.equipment_id,
                    Equipment.project_id == project.id,
                    Equipment.workspace == pf.workspace,
                )
            )
        ).scalar_one_or_none()
        if not eq:
            raise HTTPException(
                status_code=404,
                detail="Equipment not found in this workspace.",
            )
        v = await apply_update(
            db, eq, fields,
            source="vendor",
            source_file_id=pf.id,
            user_id=user.id,
            note=f"Manual apply from vendor sheet {pf.name}",
        )
        await audit_service.log(
            db, action="vendor.manual_apply",
            user_id=user.id, project_id=project.id,
            entity_type="equipment", entity_id=eq.id,
            metadata={"file_id": pf.id, "fields": list(fields.keys()), "created_version": bool(v)},
        )
        await db.commit()
        return {"status": "applied", "equipment_id": eq.id, "new_version_created": bool(v)}

    if payload.new_client_tag:
        tag = payload.new_client_tag.strip()
        if not tag:
            raise HTTPException(status_code=400, detail="client_tag is empty.")
        # Re-check no equipment with that tag already exists (case-insensitive on
        # the workspace) — saves the user from accidental duplicate creation.
        existing = (
            await db.execute(
                select(Equipment).where(
                    Equipment.project_id == project.id,
                    Equipment.workspace == pf.workspace,
                    Equipment.client_tag.ilike(tag),
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Equipment with tag {existing.client_tag!r} already exists in this workspace. Use equipment_id instead.",
            )
        eq = await create_equipment_from_sync(
            db, project.id, tag, fields,
            source="vendor",
            source_file_id=pf.id,
            user_id=user.id,
            workspace=pf.workspace,
        )
        await audit_service.log(
            db, action="vendor.manual_create",
            user_id=user.id, project_id=project.id,
            entity_type="equipment", entity_id=eq.id,
            metadata={"file_id": pf.id, "client_tag": tag, "workspace": pf.workspace},
        )
        await db.commit()
        return {"status": "created", "equipment_id": eq.id, "client_tag": tag}

    raise HTTPException(
        status_code=400,
        detail="Provide either equipment_id (to update an existing row) or new_client_tag (to create one).",
    )


@router.delete("/projects/{project_id}/files/{file_id}", status_code=204)
async def delete_file(
    file_id: int,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    """Remove a synced file from the project.

    Effects:
      - Deletes the ``project_files`` row.
      - Cascade-deletes its ``file_extractions`` (the raw vision JSON, etc).
      - Removes the local cached copy at ``local_path`` if it still exists.
      - Equipment rows that previously referenced this file via
        ``last_source_file_id`` / ``EquipmentVersion.source_file_id`` are
        kept (FK is ``ON DELETE SET NULL``), so version history stays
        intact — just the link to the now-deleted file is severed.

    The corresponding OneDrive selection (if any) is left alone, so the
    next sync would re-download the file. To stop that, deselect it on
    the OneDrive page first.
    """
    pf = (
        await db.execute(
            select(ProjectFile).where(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if not pf:
        raise HTTPException(status_code=404, detail="File not found")

    # Snapshot for the audit log + local file deletion (the SQLAlchemy
    # instance becomes unusable after delete).
    snap = {
        "id": pf.id,
        "name": pf.name,
        "onedrive_item_id": pf.onedrive_item_id,
        "onedrive_path": pf.onedrive_path,
        "folder_category": pf.folder_category,
        "size_bytes": pf.size_bytes,
        "local_path": pf.local_path,
    }

    await db.delete(pf)
    await db.flush()

    # Best-effort local cache cleanup. Don't fail the request on IO errors —
    # the DB row is gone and that's the authoritative state.
    local_removed = False
    local_path = snap["local_path"]
    if local_path:
        try:
            p = Path(local_path)
            if p.exists() and p.is_file():
                p.unlink()
                local_removed = True
        except OSError:
            local_removed = False

    await audit_service.log(
        db,
        action="file.delete",
        user_id=user.id,
        project_id=project.id,
        metadata={**snap, "local_removed": local_removed},
    )
    await db.commit()
    # 204 No Content
    return None


class BulkDeleteFilesRequest(BaseModel):
    ids: list[int]


@router.post("/projects/{project_id}/files/bulk-delete")
async def bulk_delete_files(
    payload: BulkDeleteFilesRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    """Delete many project files in one transaction.

    Same per-file semantics as ``DELETE /projects/{project_id}/files/{file_id}``:
    removes the row, cascade-deletes its extractions, best-effort deletes
    the local cached copy. Only rows belonging to ``project_id`` are
    touched, so a stale UI can't wipe files from other projects. Returns
    ``{deleted, not_found, local_removed}`` for a summary toast.
    """
    if not payload.ids:
        return {"deleted": 0, "not_found": 0, "local_removed": 0}

    rows = (
        await db.execute(
            select(ProjectFile).where(
                ProjectFile.id.in_(payload.ids),
                ProjectFile.project_id == project.id,
            )
        )
    ).scalars().all()
    found_ids = {r.id for r in rows}
    not_found = len([i for i in payload.ids if i not in found_ids])

    snaps = [
        {
            "id": r.id,
            "name": r.name,
            "onedrive_item_id": r.onedrive_item_id,
            "onedrive_path": r.onedrive_path,
            "folder_category": r.folder_category,
            "size_bytes": r.size_bytes,
            "local_path": r.local_path,
        }
        for r in rows
    ]

    for r in rows:
        await db.delete(r)
    await db.flush()

    local_removed = 0
    for snap in snaps:
        lp = snap["local_path"]
        if not lp:
            continue
        try:
            p = Path(lp)
            if p.exists() and p.is_file():
                p.unlink()
                local_removed += 1
        except OSError:
            pass

    await audit_service.log(
        db,
        action="file.bulk_delete",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "deleted": len(found_ids),
            "not_found": not_found,
            "local_removed": local_removed,
            "files": snaps,
        },
    )
    await db.commit()
    return {
        "deleted": len(found_ids),
        "not_found": not_found,
        "local_removed": local_removed,
    }
