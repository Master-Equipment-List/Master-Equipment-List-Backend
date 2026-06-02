from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import CurrentUser, DbSession, project_access
from app.models import Project
from app.services import sync_service

router = APIRouter()


@router.post("/projects/{project_id}/sync")
async def trigger_sync(
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
    force: bool = Query(
        False,
        description="Re-download and re-parse even if the OneDrive lastModifiedDateTime hasn't changed.",
    ),
):
    """Run sync inline. By default, files whose OneDrive modified timestamp
    matches the last sync are skipped. Pass `force=true` to re-process all
    selected items."""
    summary = await sync_service.run_sync(db, project, user_id=user.id, force=force)
    return summary


@router.post("/projects/{project_id}/sync/item")
async def trigger_single_item_sync(
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
    item_id: str = Query(..., description="OneDrive drive-item id to sync."),
    force: bool = Query(
        False,
        description="Re-download and re-parse even if the file's OneDrive modified timestamp hasn't changed.",
    ),
):
    """Sync ONE OneDrive item directly without modifying the project's
    persistent selection. If the item is a folder, walks it recursively.
    """
    try:
        summary = await sync_service.sync_single_item(
            db, project, item_id, user_id=user.id, force=force,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return summary
