"""Deep wipe of a project's CONTENT, keeping the shell.

Removes:
    - equipment (every row)
    - equipment_versions (cascade from equipment)
    - project_files
    - file_extractions (cascade from project_files)
    - project_onedrive_selections (folder picks)
    - audit_logs
    - local file cache (storage/project_*)

Keeps:
    - users
    - onedrive_tokens (org-level OAuth)
    - projects (the shell — id, name, type, OneDrive root binding)
    - project_members (team)
    - alembic_version

Use this when you want to re-seed / re-sync a project from scratch without
losing the project itself or having to re-pick OneDrive folders... wait,
actually we DO clear onedrive selections so you re-pick them. The OneDrive
ROOT (`onedrive_root_path` on Project) is preserved on the project row.
"""
from __future__ import annotations

import asyncio
import shutil
import sys

from sqlalchemy import delete, func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models import (
    AuditLog, Equipment, EquipmentVersion, FileExtraction, OneDriveToken,
    Project, ProjectFile, ProjectMember, ProjectOneDriveSelection, User,
)


async def _count(db, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def _snapshot(db) -> dict[str, int]:
    return {
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


async def main() -> None:
    print("== Before ==")
    async with AsyncSessionLocal() as db:
        before = await _snapshot(db)
        for k, v in before.items():
            print(f"  {k:30s} {v}")

    print()
    print("== Wiping project content (project shell + team + OneDrive root preserved) ==")
    async with AsyncSessionLocal() as db:
        # Order: delete children before parents. Most have cascades, but
        # explicit deletes give us accurate before/after counts.
        await db.execute(delete(EquipmentVersion))
        await db.execute(delete(Equipment))
        await db.execute(delete(FileExtraction))
        await db.execute(delete(ProjectFile))
        await db.execute(delete(ProjectOneDriveSelection))
        await db.execute(delete(AuditLog))
        await db.commit()

    print()
    print("== After ==")
    async with AsyncSessionLocal() as db:
        after = await _snapshot(db)
        for k, v in after.items():
            flag = "  <- changed" if before[k] != v else ""
            print(f"  {k:30s} {v}{flag}")

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
    print("Done. Open the OneDrive page on a project to re-pick folders + re-sync.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
