from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.core.security import hash_password
from app.deps import AdminUser, DbSession
from app.models import User
from app.schemas.common import Page
from app.schemas.user import UserOut, UserUpdate
from app.services import audit_service

router = APIRouter()


@router.get("", response_model=Page[UserOut])
async def list_users(
    db: DbSession,
    _admin: AdminUser,
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    rows = (
        await db.execute(select(User).order_by(User.id).limit(limit).offset(offset))
    ).scalars().all()
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, db: DbSession, _admin: AdminUser):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: DbSession,
    admin: AdminUser,
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(user, k, v)
    await audit_service.log(
        db,
        action="user.update",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        metadata={"changes": data},
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/{user_id}/password", response_model=UserOut)
async def reset_password(
    user_id: int,
    new_password: str,
    db: DbSession,
    admin: AdminUser,
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password too short")
    user.hashed_password = hash_password(new_password)
    await audit_service.log(
        db,
        action="user.password_reset",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
    )
    await db.commit()
    await db.refresh(user)
    return user
