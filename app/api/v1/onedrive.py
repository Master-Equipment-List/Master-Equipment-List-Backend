import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select

from app.config import settings
from app.deps import AdminUser, CurrentUser, DbSession, project_access
from app.models import OneDriveToken, Project, ProjectOneDriveSelection
from app.schemas.onedrive import (
    BrowseResponse,
    DriveItem,
    OAuthStartResponse,
    SelectionOut,
    SelectionRequest,
)
from app.services import audit_service
from app.services import onedrive_service

router = APIRouter()


# --- Org-level OAuth ---

@router.get("/onedrive/status")
async def onedrive_status(db: DbSession, _admin: AdminUser):
    return await onedrive_service.verify_connection(db)


@router.delete("/onedrive/disconnect", status_code=204)
async def onedrive_disconnect(db: DbSession, admin: AdminUser):
    await db.execute(
        delete(OneDriveToken).where(OneDriveToken.tenant_id == settings.MS_TENANT_ID)
    )
    await audit_service.log(db, action="onedrive.disconnect", user_id=admin.id)
    await db.commit()


@router.get("/onedrive/me/browse", response_model=list[DriveItem])
async def onedrive_me_browse(
    db: DbSession,
    _user: CurrentUser,
    item_id: str | None = Query(None, description="Drill into this folder id."),
    drive_id: str | None = Query(None, description="Target a non-default drive."),
    path: str | None = Query(None, description="Absolute drive path, e.g. /Documents/Project."),
):
    """Project-independent OneDrive browser. Used by the folder-picker UI
    when configuring a project's OneDrive root."""
    try:
        items = await onedrive_service.browse_my_drive(
            db, item_id=item_id, drive_id=drive_id, path=path
        )
    except onedrive_service.OneDriveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [DriveItem(**i) for i in items]


@router.get("/onedrive/me/shared", response_model=list[DriveItem])
async def onedrive_me_shared(db: DbSession, _user: CurrentUser):
    """List items that were shared with the connected account. Each returned
    item carries `is_shortcut=True` + `remote_item_id` + `remote_drive_id`,
    suitable for use as a project root."""
    token = await onedrive_service.get_valid_token(db)
    try:
        async with __import__("httpx").AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/drive/sharedWithMe?$top=200",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=resp.text)
        raw = resp.json().get("value", [])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    out: list[DriveItem] = []
    for it in raw:
        # sharedWithMe items always have remoteItem
        normalized = onedrive_service._normalize_drive_item(it)
        out.append(DriveItem(**normalized))
    return out


@router.get("/onedrive/oauth/start", response_model=OAuthStartResponse)
async def onedrive_oauth_start(_admin: AdminUser):
    try:
        url, state = onedrive_service.build_auth_url()
    except onedrive_service.OneDriveNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e))
    return OAuthStartResponse(authorization_url=url, state=state)


@router.get("/onedrive/oauth/callback")
async def onedrive_oauth_callback(
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Public callback hit by Microsoft after consent. Performs the
    server-side code-for-token exchange, then redirects the user back to the
    frontend admin page with a status query param so the UI can show
    success/error.
    """
    fe_base = settings.FRONTEND_BASE_URL.rstrip("/")
    target = f"{fe_base}/admin/onedrive"

    if error:
        qs = urllib.parse.urlencode({
            "status": "error",
            "error": error,
            "message": error_description or error,
        })
        return RedirectResponse(url=f"{target}?{qs}", status_code=302)

    if not code:
        qs = urllib.parse.urlencode({"status": "error", "message": "Missing authorization code"})
        return RedirectResponse(url=f"{target}?{qs}", status_code=302)

    try:
        token_data = await onedrive_service.exchange_code(code)
    except onedrive_service.OneDriveError as e:
        qs = urllib.parse.urlencode({"status": "error", "message": str(e)})
        return RedirectResponse(url=f"{target}?{qs}", status_code=302)

    account_email = await onedrive_service._fetch_account_email(token_data["access_token"])
    await onedrive_service.save_token(db, token_data, account_email)
    await db.commit()
    return RedirectResponse(url=f"{target}?status=connected", status_code=302)


# --- Per-project browse + selection ---

@router.get("/projects/{project_id}/onedrive/browse", response_model=BrowseResponse)
async def browse(
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("viewer")),
    item_id: str | None = Query(None, description="Folder item id to drill into; omit for project root."),
    workspace: str = Query("topside", description="Which workspace's OneDrive root to browse: 'topside' or 'marine'."),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        items = await onedrive_service.list_children(db, project, item_id=item_id, workspace=workspace)
    except onedrive_service.OneDriveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    root_path, _, _ = project.onedrive_root_for(workspace)
    # Graph itself paginates via opaque continuation tokens (not offset-
    # based), so list_children already walks every page internally. We
    # apply our own limit/offset on top of that full listing here — folder
    # listings are bounded enough in practice that this is simpler than
    # threading Graph's @odata.nextLink cursor through our own API.
    total = len(items)
    page_items = items[offset:offset + limit]
    return BrowseResponse(
        project_id=project.id,
        root_path=root_path,
        items=[DriveItem(**i) for i in page_items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/projects/{project_id}/onedrive/selection", response_model=list[SelectionOut])
async def set_selection(
    payload: SelectionRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
    workspace: str = Query("topside", description="Which workspace these selections belong to."),
):
    if payload.replace:
        # Only delete THIS workspace's selections — the other workspace's
        # picks stay intact.
        await db.execute(
            delete(ProjectOneDriveSelection).where(
                ProjectOneDriveSelection.project_id == project.id,
                ProjectOneDriveSelection.workspace == workspace,
            )
        )

    created: list[ProjectOneDriveSelection] = []
    for it in payload.items:
        row = ProjectOneDriveSelection(
            project_id=project.id,
            workspace=workspace,
            item_id=it.item_id,
            item_path=it.item_path,
            item_type=it.item_type,
            name=it.name,
            size_bytes=it.size_bytes,
        )
        db.add(row)
        created.append(row)
    await audit_service.log(
        db,
        action="onedrive.selection_set",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "count": len(payload.items),
            "replace": payload.replace,
            "workspace": workspace,
        },
    )
    await db.commit()
    for c in created:
        await db.refresh(c)
    return created


@router.get("/projects/{project_id}/onedrive/selection", response_model=list[SelectionOut])
async def get_selection(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    workspace: str | None = Query(None, description="Filter to one workspace; omit to return all."),
):
    stmt = select(ProjectOneDriveSelection).where(
        ProjectOneDriveSelection.project_id == project.id
    )
    if workspace:
        stmt = stmt.where(ProjectOneDriveSelection.workspace == workspace)
    rows = (await db.execute(stmt)).scalars().all()
    return rows
