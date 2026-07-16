from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession, project_access
from app.models import Project, ProjectMember, User
from app.schemas.common import Page
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberAdd,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
)
from app.services import audit_service

router = APIRouter()


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate, db: DbSession, user: CurrentUser):
    project = Project(
        name=payload.name,
        code=payload.code,
        project_type=payload.project_type,
        description=payload.description,
        client=payload.client,
        facility=payload.facility,
        location=payload.location,
        onedrive_root_path=payload.onedrive_root_path,
        onedrive_drive_id=payload.onedrive_drive_id,
        onedrive_root_item_id=payload.onedrive_root_item_id,
        created_by_id=user.id,
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Project code already exists")

    # Creator becomes project admin automatically
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="admin"))
    await audit_service.log(
        db,
        action="project.create",
        user_id=user.id,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        metadata={"type": project.project_type},
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: DbSession, user: CurrentUser):
    if user.is_superuser or user.role == "admin":
        rows = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
        return rows

    stmt = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.id)
    )
    return (await db.execute(stmt)).scalars().all()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project: Project = Depends(project_access("viewer"))):
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    payload: ProjectUpdate,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(project, k, v)
    await audit_service.log(
        db,
        action="project.update",
        user_id=user.id,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
        metadata={"changes": data},
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    await audit_service.log(
        db,
        action="project.delete",
        user_id=user.id,
        project_id=project.id,
        entity_type="project",
        entity_id=project.id,
    )
    await db.delete(project)
    await db.commit()


# --- Members ---

@router.get("/{project_id}/members", response_model=Page[ProjectMemberOut])
async def list_members(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    stmt = select(ProjectMember).where(ProjectMember.project_id == project.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(ProjectMember.id).limit(limit).offset(offset))
    ).scalars().all()
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.post("/{project_id}/members", response_model=ProjectMemberOut, status_code=201)
async def add_member(
    payload: ProjectMemberAdd,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    target = (
        await db.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.role = payload.role
        member = existing
    else:
        member = ProjectMember(
            project_id=project.id, user_id=payload.user_id, role=payload.role
        )
        db.add(member)

    await audit_service.log(
        db,
        action="project.member_add",
        user_id=user.id,
        project_id=project.id,
        entity_type="project_member",
        entity_id=payload.user_id,
        metadata={"role": payload.role},
    )
    await db.commit()
    await db.refresh(member)
    return member


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    user_id: int,
    db: DbSession,
    actor: CurrentUser,
    project: Project = Depends(project_access("admin")),
):
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.delete(member)
    await audit_service.log(
        db,
        action="project.member_remove",
        user_id=actor.id,
        project_id=project.id,
        entity_type="project_member",
        entity_id=user_id,
    )
    await db.commit()
