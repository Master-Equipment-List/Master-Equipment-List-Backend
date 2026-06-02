"""Seed a Topside project from the provided POC Excel.

Creates (or updates) a "POC Topside" project owned by the first admin
and imports every equipment row from
`MEL POC data/20171-SPOG-80000-ME-LS-0001_Z1_Topside Eqipment List.xlsx`.

Run after `alembic upgrade head` and `scripts/create_admin.py`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.extractors.topside_excel import extract_equipment_rows
from app.models import Equipment, Project, ProjectMember, User
from app.services.version_service import record_initial_version


POC_EXCEL_DEFAULT = Path(
    r"D:\targeticon\Master Equipment List\MEL POC data\20171-SPOG-80000-ME-LS-0001_Z1_Topside Eqipment List.xlsx"
)
PROJECT_NAME = "POC Topside (20171)"
PROJECT_CODE = "20171-SPOG"


async def main(excel_path: Path) -> None:
    if not excel_path.exists():
        print(f"Excel file not found: {excel_path}")
        sys.exit(2)

    rows = extract_equipment_rows(str(excel_path))
    if not rows:
        print("No equipment rows extracted — check the file format.")
        sys.exit(3)
    print(f"Parsed {len(rows)} equipment rows from {excel_path.name}")

    async with AsyncSessionLocal() as db:
        admin = (
            await db.execute(select(User).where(User.email == settings.FIRST_ADMIN_EMAIL))
        ).scalar_one_or_none()
        if not admin:
            print("First admin not found — run scripts.create_admin first.")
            sys.exit(4)

        project = (
            await db.execute(select(Project).where(Project.code == PROJECT_CODE))
        ).scalar_one_or_none()
        if not project:
            project = Project(
                name=PROJECT_NAME,
                code=PROJECT_CODE,
                project_type="topside",
                description="Pre-loaded POC Topside data (20171 / FPSO A1).",
                client="ONGC",
                facility="FPSO A1",
                location="INDIA",
                created_by_id=admin.id,
            )
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=admin.id, role="admin"))
            await db.flush()
            print(f"Created project #{project.id}: {project.name}")
        else:
            print(f"Reusing project #{project.id}: {project.name}")

        created = 0
        skipped = 0
        for row in rows:
            tag = row["client_tag"]
            existing = (
                await db.execute(
                    select(Equipment).where(
                        Equipment.project_id == project.id,
                        Equipment.client_tag == tag,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                skipped += 1
                continue

            raw_extra = row.pop("__raw", {})
            eq = Equipment(
                project_id=project.id,
                data={"raw": raw_extra},
                created_by_id=admin.id,
                **{k: v for k, v in row.items() if v is not None},
            )
            db.add(eq)
            await db.flush()
            await record_initial_version(db, eq, source="seed", user_id=admin.id)
            created += 1

        await db.commit()
        print(f"Equipment created: {created}, skipped (already existed): {skipped}")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else POC_EXCEL_DEFAULT
    asyncio.run(main(p))
