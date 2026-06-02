"""Delete only the files data — synced files, parsed extractions, and the
local file cache. Useful for re-testing the extraction pipeline against the
same OneDrive selection without losing the project, team, or equipment.

Removes:
    - file_extractions (cascade from project_files)
    - project_files
    - local file cache in storage/project_*

Preserves:
    - users
    - onedrive_tokens
    - projects + project_members
    - project_onedrive_selections  (your folder picks stay intact)
    - equipment + equipment_versions  (manually-entered / imported rows)
    - audit_logs
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
    n = (await db.execute(select(func.count()).select_from(model))).scalar_one()
    return int(n)


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
    print("== Wiping project_files + file_extractions ==")
    async with AsyncSessionLocal() as db:
        # file_extractions has ON DELETE CASCADE from project_files, but we
        # delete it explicitly first so the row count print is accurate.
        await db.execute(delete(FileExtraction))
        await db.execute(delete(ProjectFile))
        await db.commit()

    print()
    print("== After ==")
    async with AsyncSessionLocal() as db:
        after = await _snapshot(db)
        for k, v in after.items():
            flag = "  <- changed" if before[k] != v else ""
            print(f"  {k:30s} {v}{flag}")

    # Local file cache — only the per-project downloads, not the whole storage dir
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
    print("Done. OneDrive selections are still in place — open a project and "
          "run sync to re-download + re-extract.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
