"""Diagnostic: verify the stored OneDrive token, list drive root + sharedWithMe.

Run:
    python -m scripts.onedrive_diag
"""
from __future__ import annotations

import asyncio
import json

import httpx
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import OneDriveToken
from app.services.onedrive_service import get_valid_token


GRAPH = "https://graph.microsoft.com/v1.0"


def _hr(title: str) -> None:
    print(f"\n=== {title} ===")


def _show(items, max_items=15):
    for i, it in enumerate(items[:max_items]):
        kind = (
            "folder" if "folder" in it else
            "shortcut" if "remoteItem" in it else
            "file"
        )
        size = it.get("size") or it.get("remoteItem", {}).get("size") or 0
        # surface the parent driveId for shortcuts (so user knows where to point)
        remote = it.get("remoteItem") or {}
        rid = remote.get("id")
        rdrive = remote.get("parentReference", {}).get("driveId") if remote else None
        line = f"  {i+1:2d}. [{kind:8}] {it.get('name')}  (size {size})"
        if rid:
            line += f"\n        remoteItem.id      = {rid}"
            line += f"\n        remoteItem.driveId = {rdrive}"
        print(line)
    if len(items) > max_items:
        print(f"  … and {len(items) - max_items} more")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        tok = (await db.execute(select(OneDriveToken))).scalar_one_or_none()
        if not tok:
            print("No OneDrive token in DB. Connect at /admin/onedrive first.")
            return
        _hr("Stored token")
        print(f"tenant     = {tok.tenant_id}")
        print(f"account    = {tok.account_email}")
        print(f"expires_at = {tok.expires_at.isoformat()}")
        print(f"scope      = {tok.scope}")

        access = await get_valid_token(db)
        await db.commit()
        headers = {"Authorization": f"Bearer {access}"}

        async with httpx.AsyncClient(timeout=30) as client:
            _hr("GET /me  (who am I?)")
            r = await client.get(f"{GRAPH}/me", headers=headers)
            print(f"status {r.status_code}")
            if r.status_code == 200:
                me = r.json()
                print(f"  upn         = {me.get('userPrincipalName')}")
                print(f"  displayName = {me.get('displayName')}")
                print(f"  mail        = {me.get('mail')}")
                print(f"  id          = {me.get('id')}")
            else:
                print(r.text)

            _hr("GET /me/drive  (your default OneDrive)")
            r = await client.get(f"{GRAPH}/me/drive", headers=headers)
            print(f"status {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                print(f"  drive id   = {d.get('id')}")
                print(f"  drive name = {d.get('name')}")
                print(f"  drive type = {d.get('driveType')}")
                print(f"  owner      = {(d.get('owner') or {}).get('user', {}).get('displayName')}")
            else:
                print(r.text)

            _hr("GET /me/drive/root/children  (top of YOUR OneDrive)")
            r = await client.get(f"{GRAPH}/me/drive/root/children?$top=100", headers=headers)
            print(f"status {r.status_code}")
            if r.status_code == 200:
                items = r.json().get("value", [])
                print(f"  {len(items)} item(s) at your drive root:")
                _show(items)
            else:
                print(r.text)

            _hr("GET /me/drive/sharedWithMe  (folders/files shared with you)")
            r = await client.get(f"{GRAPH}/me/drive/sharedWithMe?$top=100", headers=headers)
            print(f"status {r.status_code}")
            if r.status_code == 200:
                items = r.json().get("value", [])
                print(f"  {len(items)} shared item(s):")
                _show(items)
            else:
                print(r.text)

            # Try the path the user configured
            _hr("Sanity check: try the path '/MEL POC data/Marine_20171' on /me/drive")
            import urllib.parse
            path = "MEL POC data/Marine_20171"
            url = f"{GRAPH}/me/drive/root:/{urllib.parse.quote(path)}:/children"
            r = await client.get(url, headers=headers)
            print(f"status {r.status_code}")
            if r.status_code != 200:
                print(r.text[:500])
            else:
                items = r.json().get("value", [])
                print(f"  {len(items)} children:")
                _show(items)


if __name__ == "__main__":
    asyncio.run(main())
