"""Shared FastAPI dependencies (auth + access control)."""
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db
from app.models import Project, ProjectMember, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login", auto_error=True)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type")

    user_id_raw = payload.get("sub")
    if not user_id_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    user = (await db.execute(select(User).where(User.id == int(user_id_raw)))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin" and not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


_PROJECT_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


async def _load_project_with_role(
    db: AsyncSession, project_id: int, user: User
) -> tuple[Project, str | None]:
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if user.is_superuser or user.role == "admin":
        return project, "admin"

    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    return project, (member.role if member else None)


def project_access(required: str = "viewer"):
    """Dependency factory: require a project role >= the given level.

    Usage:  Depends(project_access("editor"))
    """

    async def _dep(
        project_id: Annotated[int, Path(...)],
        db: DbSession,
        user: CurrentUser,
    ) -> Project:
        project, role = await _load_project_with_role(db, project_id, user)
        if role is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a project member")
        if _PROJECT_ROLE_RANK[role] < _PROJECT_ROLE_RANK[required]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Project role '{required}' required",
            )
        return project

    return _dep
