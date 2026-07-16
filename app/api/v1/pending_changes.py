from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, project_access
from app.models import Equipment, EquipmentPendingChange, Project, ProjectFile, User
from app.schemas.common import Page
from app.schemas.pending_change import (
    ApprovePendingChangeRequest,
    ConfirmNewResponse,
    PendingChangeOut,
    ResolvePendingChangeResponse,
)
from app.services import audit_service
from app.services.pending_change_service import (
    approve_pending_change,
    reject_pending_change,
    resolve_duplicate_as_merge,
    resolve_duplicate_as_new,
)

router = APIRouter()


@router.get(
    "/projects/{project_id}/equipment/pending",
    response_model=Page[PendingChangeOut],
)
async def list_pending_changes(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    workspace: str | None = Query(None, description="Filter to one workspace; omit for all."),
    status: str = Query(
        "pending",
        regex="^(pending|approved|rejected|all)$",
        description="Filter by status; 'all' returns the full history.",
    ),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    """Sync-proposed changes for this project — the review queue plus,
    when asked, the resolved history (who approved/rejected and when).

    Anyone with project access can SEE this; only a project admin can
    approve/reject (see the endpoints below).
    """
    stmt = select(EquipmentPendingChange).where(
        EquipmentPendingChange.project_id == project.id
    )
    if workspace:
        stmt = stmt.where(EquipmentPendingChange.workspace == workspace)
    if status != "all":
        stmt = stmt.where(EquipmentPendingChange.status == status)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(EquipmentPendingChange.updated_at.desc()).limit(limit).offset(offset)
    pending = (await db.execute(stmt)).scalars().all()
    if not pending:
        return Page(items=[], total=total, limit=limit, offset=offset)

    eq_ids = {p.equipment_id for p in pending}
    equipment_by_id = {
        e.id: e
        for e in (
            await db.execute(select(Equipment).where(Equipment.id.in_(eq_ids)))
        ).scalars().all()
    }

    file_ids = {p.source_file_id for p in pending if p.source_file_id}
    file_name_by_id: dict[int, str] = {}
    if file_ids:
        files = (
            await db.execute(select(ProjectFile).where(ProjectFile.id.in_(file_ids)))
        ).scalars().all()
        file_name_by_id = {f.id: f.name for f in files}

    user_ids = {p.created_by_id for p in pending if p.created_by_id} | {
        p.resolved_by_id for p in pending if p.resolved_by_id
    }
    user_name_by_id: dict[int, str] = {}
    if user_ids:
        users = (
            await db.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
        user_name_by_id = {u.id: (u.full_name or u.email) for u in users}

    out: list[PendingChangeOut] = []
    for p in pending:
        eq = equipment_by_id.get(p.equipment_id)
        if not eq:
            continue  # orphaned row (equipment deleted) — shouldn't happen, CASCADE handles it
        out.append(
            PendingChangeOut(
                id=p.id,
                equipment_id=p.equipment_id,
                client_tag=eq.client_tag,
                description=eq.description,
                workspace=p.workspace,
                source=p.source,
                source_file_id=p.source_file_id,
                source_file_name=file_name_by_id.get(p.source_file_id) if p.source_file_id else None,
                kind=p.kind,
                new_tag=p.new_tag,
                proposed_fields=p.proposed_fields,
                status=p.status,
                created_by_name=user_name_by_id.get(p.created_by_id) if p.created_by_id else None,
                resolved_by_name=user_name_by_id.get(p.resolved_by_id) if p.resolved_by_id else None,
                resolved_at=p.resolved_at,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    return Page(items=out, total=total, limit=limit, offset=offset)


async def _get_pending_or_404(
    db: DbSession, project_id: int, pending_id: int
) -> EquipmentPendingChange:
    pc = await db.get(EquipmentPendingChange, pending_id)
    if not pc or pc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Pending change not found")
    if pc.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already resolved ({pc.status})")
    return pc


@router.post(
    "/projects/{project_id}/equipment/pending/{pending_id}/approve",
    response_model=ResolvePendingChangeResponse,
)
async def approve_pending_change_route(
    pending_id: int,
    payload: ApprovePendingChangeRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    """Apply the admin-selected subset of a pending change's fields, then
    mark it resolved (kept as history). Restricted to project admins.
    Only for kind="update" — see confirm-new/confirm-duplicate below for
    kind="possible_duplicate"."""
    pc = await _get_pending_or_404(db, project.id, pending_id)
    if pc.kind != "update":
        raise HTTPException(
            status_code=400,
            detail="This is a possible-duplicate item — use confirm-new or confirm-duplicate instead.",
        )
    eq = await db.get(Equipment, pc.equipment_id)
    if not eq:
        await reject_pending_change(db, pc, user_id=user.id)
        await db.commit()
        raise HTTPException(status_code=404, detail="Equipment row no longer exists")

    result = await approve_pending_change(
        db, pc, eq,
        accepted_fields=payload.accepted_fields,
        user_id=user.id,
    )
    await audit_service.log(
        db,
        action="equipment.pending_change_approved",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "equipment_id": eq.id,
            "client_tag": eq.client_tag,
            "accepted_fields": payload.accepted_fields,
            "applied_fields": result["applied_fields"],
        },
    )
    await db.commit()
    return ResolvePendingChangeResponse(applied_fields=result["applied_fields"])


def _require_duplicate_kind(pc: EquipmentPendingChange) -> None:
    if pc.kind != "possible_duplicate":
        raise HTTPException(
            status_code=400,
            detail="This is a normal update item — use approve/reject instead.",
        )


@router.post(
    "/projects/{project_id}/equipment/pending/{pending_id}/confirm-new",
    response_model=ConfirmNewResponse,
)
async def confirm_new_route(
    pending_id: int,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    """Admin confirmed: this is genuinely new equipment, not a duplicate of
    the flagged candidate. Creates it under the incoming tag. Restricted
    to project admins."""
    pc = await _get_pending_or_404(db, project.id, pending_id)
    _require_duplicate_kind(pc)

    eq = await resolve_duplicate_as_new(db, pc, project.id, user_id=user.id)
    await audit_service.log(
        db,
        action="equipment.pending_duplicate_confirmed_new",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "new_equipment_id": eq.id,
            "client_tag": eq.client_tag,
            "candidate_equipment_id": pc.equipment_id,
        },
    )
    await db.commit()
    return ConfirmNewResponse(equipment_id=eq.id, client_tag=eq.client_tag)


@router.post(
    "/projects/{project_id}/equipment/pending/{pending_id}/confirm-duplicate",
    response_model=ResolvePendingChangeResponse,
)
async def confirm_duplicate_route(
    pending_id: int,
    payload: ApprovePendingChangeRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    """Admin confirmed: this IS the same equipment as the flagged candidate,
    just under a different/corrected tag. Applies the admin-selected subset
    of fields onto the candidate (its own tag is never renamed). Restricted
    to project admins."""
    pc = await _get_pending_or_404(db, project.id, pending_id)
    _require_duplicate_kind(pc)

    candidate = await db.get(Equipment, pc.equipment_id)
    if not candidate:
        await reject_pending_change(db, pc, user_id=user.id)
        await db.commit()
        raise HTTPException(status_code=404, detail="Candidate equipment row no longer exists")

    result = await resolve_duplicate_as_merge(
        db, pc, candidate,
        accepted_fields=payload.accepted_fields,
        user_id=user.id,
    )
    await audit_service.log(
        db,
        action="equipment.pending_duplicate_confirmed_duplicate",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "candidate_equipment_id": candidate.id,
            "candidate_client_tag": candidate.client_tag,
            "incoming_tag": pc.new_tag,
            "accepted_fields": payload.accepted_fields,
            "applied_fields": result["applied_fields"],
        },
    )
    await db.commit()
    return ResolvePendingChangeResponse(applied_fields=result["applied_fields"])


@router.post("/projects/{project_id}/equipment/pending/{pending_id}/reject", status_code=204)
async def reject_pending_change_route(
    pending_id: int,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    """Mark a pending change rejected (kept as history) without touching
    the equipment row. Restricted to project admins."""
    pc = await _get_pending_or_404(db, project.id, pending_id)
    equipment_id = pc.equipment_id
    await reject_pending_change(db, pc, user_id=user.id)
    await audit_service.log(
        db,
        action="equipment.pending_change_rejected",
        user_id=user.id,
        project_id=project.id,
        metadata={"equipment_id": equipment_id},
    )
    await db.commit()
