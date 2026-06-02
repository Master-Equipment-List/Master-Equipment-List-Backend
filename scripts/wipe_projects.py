"""Delete every project and everything cascaded from them.

Removes:
    - projects (cascades to project_members, equipment, project_files,
      file_extractions, equipment_versions, project_onedrive_selections)
    - any audit_logs whose project_id is set (history of project work)
    - local file cache in storage/project_*

Preserves:
    - users
    - onedrive_tokens (organization-level OAuth)
    - alembic_version (DB schema state)
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from sqlalchemy import delete, func, select, text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models import (
    AuditLog, Equipment, EquipmentVersion, FileExtraction, OneDriveToken,
    Project, ProjectFile, ProjectMember, ProjectOneDriveSelection, User,
)


async def _count(db, model) -> int:
    n = (await db.execute(select(func.count()).select_from(model))).scalar_one()
    return int(n)


async def main() -> None:
    print("== Before ==")
    async with AsyncSessionLocal() as db:
        before = {
            "users":                       await _count(db, User),
            "onedrive_tokens":             await _count(db, OneDriveToken),
            "projects":                    await _count(db, Project),
            "project_members":             await _count(db, ProjectMember),
            "project_onedrive_selections": await _count(db, ProjectOneDriveSelection),
            "project_files":               await _count(db, ProjectFile),
            "file_extractions":            await _count(db, FileExtraction),
            "equipment":                   await _count(db, Equipment),
            "equipment_versions":          await _count(db, EquipmentVersion),
            "audit_logs":                  await _count(db, AuditLog),
        }
        for k, v in before.items():
            print(f"  {k:30s} {v}")

    print()
    print("== Wiping projects + audit logs (users + onedrive_tokens kept) ==")
    async with AsyncSessionLocal() as db:
        # cascade does most of the work
        await db.execute(delete(Project))
        # remove any audit log that was tied to a project (the cascade SETs
        # project_id to NULL, but the rows aren't useful without the project)
        await db.execute(delete(AuditLog))
        await db.commit()

    print()
    print("== After ==")
    async with AsyncSessionLocal() as db:
        after = {
            "users":                       await _count(db, User),
            "onedrive_tokens":             await _count(db, OneDriveToken),
            "projects":                    await _count(db, Project),
            "project_members":             await _count(db, ProjectMember),
            "project_onedrive_selections": await _count(db, ProjectOneDriveSelection),
            "project_files":               await _count(db, ProjectFile),
            "file_extractions":            await _count(db, FileExtraction),
            "equipment":                   await _count(db, Equipment),
            "equipment_versions":          await _count(db, EquipmentVersion),
            "audit_logs":                  await _count(db, AuditLog),
        }
        for k, v in after.items():
            print(f"  {k:30s} {v}")

    # Local file cache
    print()
    print("== Local file cache ==")
    storage = settings.storage_path
    removed_dirs = 0
    removed_bytes = 0
    if storage.exists():
        for child in storage.iterdir():
            if child.is_dir() and child.name.startswith("project_"):
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                shutil.rmtree(child)
                removed_dirs += 1
                removed_bytes += size
                print(f"  removed {child}  ({size/1024/1024:.2f} MB)")
    print(f"  {removed_dirs} project cache folder(s) removed, "
          f"{removed_bytes/1024/1024:.2f} MB freed")

    print()
    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
