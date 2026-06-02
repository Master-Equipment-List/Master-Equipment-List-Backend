from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
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
):
    stmt = select(ProjectFile).where(ProjectFile.project_id == project.id)
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
