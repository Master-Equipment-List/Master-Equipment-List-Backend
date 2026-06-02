from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.deps import DbSession, project_access
from app.models import Equipment, EquipmentVersion, Project
from app.schemas.equipment import EquipmentDiff, EquipmentVersionOut
from app.services.version_service import diff_snapshots, snapshot

router = APIRouter()


@router.get(
    "/projects/{project_id}/equipment/{equipment_id}/versions",
    response_model=list[EquipmentVersionOut],
)
async def list_versions(
    equipment_id: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")
    rows = (
        await db.execute(
            select(EquipmentVersion)
            .where(EquipmentVersion.equipment_id == equipment_id)
            .order_by(EquipmentVersion.version_no)
        )
    ).scalars().all()
    return rows


@router.get(
    "/projects/{project_id}/equipment/{equipment_id}/versions/{version_no}",
    response_model=EquipmentVersionOut,
)
async def get_version(
    equipment_id: int,
    version_no: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")
    v = (
        await db.execute(
            select(EquipmentVersion).where(
                EquipmentVersion.equipment_id == equipment_id,
                EquipmentVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return v


@router.get(
    "/projects/{project_id}/equipment/{equipment_id}/diff",
    response_model=EquipmentDiff,
)
async def diff(
    equipment_id: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    from_version: int = Query(..., alias="from", ge=1),
    to_version: int = Query(..., alias="to", ge=1),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")

    versions = (
        await db.execute(
            select(EquipmentVersion).where(
                EquipmentVersion.equipment_id == equipment_id,
                EquipmentVersion.version_no.in_([from_version, to_version]),
            )
        )
    ).scalars().all()
    by_no = {v.version_no: v.snapshot for v in versions}

    # "current" = the live row; expose it as version_no=eq.current_version
    if from_version == eq.current_version and from_version not in by_no:
        by_no[from_version] = snapshot(eq)
    if to_version == eq.current_version and to_version not in by_no:
        by_no[to_version] = snapshot(eq)

    if from_version not in by_no or to_version not in by_no:
        raise HTTPException(status_code=404, detail="Version not found")

    fields = diff_snapshots(by_no[from_version], by_no[to_version])
    return EquipmentDiff(
        equipment_id=equipment_id,
        from_version=from_version,
        to_version=to_version,
        fields=fields,
    )
