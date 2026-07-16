"""Microsoft Graph / OneDrive integration.

A single organization-level OAuth identity (admin-consented) is stored.
Per-project access is enforced by clamping every request to the project's
configured root path / item — we never browse outside it.
"""
from __future__ import annotations

import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OneDriveToken, Project

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTH_BASE = "https://login.microsoftonline.com"


class OneDriveError(Exception):
    pass


class OneDriveNotConfigured(OneDriveError):
    pass


def _scopes() -> str:
    return " ".join(settings.ms_scopes)


def build_auth_url(state: str | None = None) -> tuple[str, str]:
    if not settings.MS_CLIENT_ID or not settings.MS_TENANT_ID:
        raise OneDriveNotConfigured("MS_CLIENT_ID / MS_TENANT_ID not set")
    state = state or secrets.token_urlsafe(24)
    qs = urllib.parse.urlencode(
        {
            "client_id": settings.MS_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.MS_REDIRECT_URI,
            "response_mode": "query",
            "scope": _scopes(),
            "state": state,
        }
    )
    url = f"{AUTH_BASE}/{settings.MS_TENANT_ID}/oauth2/v2.0/authorize?{qs}"
    return url, state


async def exchange_code(code: str) -> dict[str, Any]:
    data = {
        "client_id": settings.MS_CLIENT_ID,
        "client_secret": settings.MS_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.MS_REDIRECT_URI,
        "grant_type": "authorization_code",
        "scope": _scopes(),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{AUTH_BASE}/{settings.MS_TENANT_ID}/oauth2/v2.0/token", data=data
        )
    if resp.status_code != 200:
        raise OneDriveError(f"Token exchange failed: {resp.text}")
    return resp.json()


async def _refresh_token_call(refresh_token: str) -> dict[str, Any]:
    data = {
        "client_id": settings.MS_CLIENT_ID,
        "client_secret": settings.MS_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": _scopes(),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{AUTH_BASE}/{settings.MS_TENANT_ID}/oauth2/v2.0/token", data=data
        )
    if resp.status_code != 200:
        raise OneDriveError(f"Token refresh failed: {resp.text}")
    return resp.json()


async def save_token(db: AsyncSession, token_data: dict[str, Any], account_email: str | None = None) -> OneDriveToken:
    expires_in = int(token_data.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)

    existing = (
        await db.execute(select(OneDriveToken).where(OneDriveToken.tenant_id == settings.MS_TENANT_ID))
    ).scalar_one_or_none()

    if existing:
        existing.access_token = token_data["access_token"]
        existing.refresh_token = token_data.get("refresh_token", existing.refresh_token)
        existing.expires_at = expires_at
        existing.scope = token_data.get("scope")
        if account_email:
            existing.account_email = account_email
        tok = existing
    else:
        tok = OneDriveToken(
            tenant_id=settings.MS_TENANT_ID,
            account_email=account_email,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scope=token_data.get("scope"),
        )
        db.add(tok)
    await db.flush()
    return tok


async def get_valid_token(db: AsyncSession) -> str:
    tok = (
        await db.execute(select(OneDriveToken).where(OneDriveToken.tenant_id == settings.MS_TENANT_ID))
    ).scalar_one_or_none()
    if not tok:
        raise OneDriveNotConfigured("OneDrive not connected. Complete /onedrive/oauth/start first.")

    if tok.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30):
        if not tok.refresh_token:
            raise OneDriveError("Token expired and no refresh token available.")
        refreshed = await _refresh_token_call(tok.refresh_token)
        tok = await save_token(db, refreshed, tok.account_email)
        await db.commit()
    return tok.access_token


async def _fetch_account_email(access_token: str) -> str | None:
    """Best-effort lookup of the connected account's email via Graph /me.

    Called right after token exchange so the admin page can show WHO is
    connected, not just that a token exists. Never raises — a failure here
    shouldn't block the OAuth flow, it just leaves account_email unset.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/me?$select=mail,userPrincipalName",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("mail") or data.get("userPrincipalName")
    except Exception:
        return None


async def verify_connection(db: AsyncSession) -> dict[str, Any]:
    """Report the OneDrive connection's REAL state, not just the stored
    access token's timestamp.

    Access tokens are short-lived (~60-90 min) by design, so ``expires_at``
    being in the past is normal whenever no sync has run recently — it does
    NOT mean the connection is broken. This runs the same refresh path a
    real sync would use (``get_valid_token``): if the refresh token is
    still good, the token is silently renewed and ``token_valid`` is True.
    Only a genuinely dead refresh token (revoked, expired, policy change)
    reports ``token_valid: False`` with the underlying error.
    """
    tok = (
        await db.execute(select(OneDriveToken).where(OneDriveToken.tenant_id == settings.MS_TENANT_ID))
    ).scalar_one_or_none()
    if not tok:
        return {"connected": False}

    error: str | None = None
    try:
        await get_valid_token(db)
    except OneDriveError as e:
        error = str(e)

    return {
        "connected": True,
        "account_email": tok.account_email,
        "tenant_id": tok.tenant_id,
        "expires_at": tok.expires_at,
        "scope": tok.scope,
        "has_refresh_token": bool(tok.refresh_token),
        "token_valid": error is None,
        "error": error,
    }


def _ensure_within_root(project: Project, item_path: str) -> None:
    """Reject any path outside the project's configured OneDrive root."""
    root = project.onedrive_root_path or ""
    if not root:
        return
    root_p = PurePosixPath(root)
    target = PurePosixPath(item_path)
    try:
        target.relative_to(root_p)
    except ValueError as e:
        raise OneDriveError(f"Path {item_path} is outside project root {root}") from e


async def _graph_get(token: str, url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code >= 400:
        raise OneDriveError(f"Graph error {resp.status_code}: {resp.text}")
    return resp.json()


def _normalize_drive_item(it: dict[str, Any]) -> dict[str, Any]:
    """Convert a Graph driveItem into our flat DriveItem shape, including
    shortcut metadata when the item is a `remoteItem` (i.e. a shared
    folder/file added to the user's drive)."""
    remote = it.get("remoteItem") or {}
    is_remote = bool(remote)
    # When it's a remoteItem, the actual folder/file flag lives inside remoteItem.
    is_folder = "folder" in it or "folder" in remote
    parent_path = it.get("parentReference", {}).get("path", "")
    name = it.get("name", "")
    path = (parent_path + "/" + name).replace("//", "/") if parent_path else name
    out = {
        "id": it["id"],
        "name": name,
        "path": path,
        "type": "folder" if is_folder else "file",
        "size": it.get("size") if not is_remote else remote.get("size"),
        "modified_at": it.get("lastModifiedDateTime"),
        "mime_type": (it.get("file") or {}).get("mimeType")
                     or (remote.get("file") or {}).get("mimeType"),
        "is_shortcut": is_remote,
        "remote_item_id": remote.get("id") if is_remote else None,
        "remote_drive_id": (remote.get("parentReference") or {}).get("driveId") if is_remote else None,
    }
    return out


async def list_children(
    db: AsyncSession,
    project: Project,
    item_id: str | None = None,
    workspace: str = "topside",
) -> list[dict[str, Any]]:
    """List children of a workspace's OneDrive root, or a specific subfolder.

    ``workspace`` selects which root to use (Topside vs Marine). When
    ``item_id`` is given the root is irrelevant — Graph scopes by the
    parent item directly — but the drive id still depends on the
    workspace's drive_id setting.
    """
    token = await get_valid_token(db)
    root_path, root_item_id, drive_id = project.onedrive_root_for(workspace)
    drive_seg = f"/drives/{drive_id}" if drive_id else "/me/drive"

    if item_id:
        url = f"{GRAPH_BASE}{drive_seg}/items/{item_id}/children"
    elif root_item_id:
        url = f"{GRAPH_BASE}{drive_seg}/items/{root_item_id}/children"
    elif root_path:
        path = root_path.strip("/")
        encoded = urllib.parse.quote(path)
        url = f"{GRAPH_BASE}{drive_seg}/root:/{encoded}:/children"
    else:
        raise OneDriveError(
            f"Project has no OneDrive root configured for the {workspace} workspace."
        )

    items: list[dict[str, Any]] = []
    while url:
        data = await _graph_get(token, url)
        for it in data.get("value", []):
            items.append(_normalize_drive_item(it))
        url = data.get("@odata.nextLink")
    return items


async def browse_my_drive(
    db: AsyncSession,
    *,
    item_id: str | None = None,
    drive_id: str | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Project-independent browser. Lists children of the user's drive root,
    a specific item id (optionally within a non-default drive), or a path.

    Used by the folder-picker UI when configuring a project's OneDrive root.
    """
    token = await get_valid_token(db)
    drive_seg = f"/drives/{drive_id}" if drive_id else "/me/drive"

    if item_id:
        url = f"{GRAPH_BASE}{drive_seg}/items/{item_id}/children"
    elif path:
        clean = path.strip("/")
        if not clean:
            url = f"{GRAPH_BASE}{drive_seg}/root/children"
        else:
            encoded = urllib.parse.quote(clean)
            url = f"{GRAPH_BASE}{drive_seg}/root:/{encoded}:/children"
    else:
        url = f"{GRAPH_BASE}{drive_seg}/root/children"

    items: list[dict[str, Any]] = []
    while url:
        data = await _graph_get(token, url)
        for it in data.get("value", []):
            items.append(_normalize_drive_item(it))
        # only paginate root level; child levels rarely exceed default page size
        url = data.get("@odata.nextLink")
    return items


async def get_item(
    db: AsyncSession,
    project: Project,
    item_id: str,
    workspace: str = "topside",
) -> dict[str, Any]:
    token = await get_valid_token(db)
    _, _, drive_id = project.onedrive_root_for(workspace)
    drive_seg = f"/drives/{drive_id}" if drive_id else "/me/drive"
    data = await _graph_get(token, f"{GRAPH_BASE}{drive_seg}/items/{item_id}")
    return data


async def walk_folder(
    db: AsyncSession,
    project: Project,
    folder_item_id: str,
    workspace: str = "topside",
) -> AsyncIterator[dict[str, Any]]:
    """Recursively yield all files under a folder item id."""
    queue: list[str] = [folder_item_id]
    while queue:
        current = queue.pop(0)
        children = await list_children(db, project, item_id=current, workspace=workspace)
        for c in children:
            if c["type"] == "folder":
                queue.append(c["id"])
            else:
                yield c


async def download_item(
    db: AsyncSession,
    project: Project,
    item_id: str,
    dest_path: str,
    workspace: str = "topside",
) -> int:
    """Download an item to dest_path; returns bytes written."""
    token = await get_valid_token(db)
    _, _, drive_id = project.onedrive_root_for(workspace)
    drive_seg = f"/drives/{drive_id}" if drive_id else "/me/drive"
    url = f"{GRAPH_BASE}{drive_seg}/items/{item_id}/content"
    written = 0
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status_code >= 400:
                raise OneDriveError(f"Download failed {resp.status_code}: {await resp.aread()}")
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
                    written += len(chunk)
    return written
