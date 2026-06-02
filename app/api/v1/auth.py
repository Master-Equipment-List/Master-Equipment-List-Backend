from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.deps import AdminUser, CurrentUser, DbSession
from app.models import User
from app.schemas.auth import AccessToken, TokenPair, TokenRefreshRequest
from app.schemas.user import UserCreate, UserOut
from app.services import audit_service

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: UserCreate, db: DbSession, _admin: AdminUser):
    """Admin-only user creation. The very first admin is bootstrapped via scripts/create_admin.py."""
    existing = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    await audit_service.log(
        db,
        action="user.create",
        user_id=_admin.id,
        entity_type="user",
        entity_id=user.id,
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(db: DbSession, form: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 password form login — `username` field carries the email."""
    user = (
        await db.execute(select(User).where(User.email == form.username))
    ).scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User inactive")

    return TokenPair(
        access_token=create_access_token(user.id, extra={"role": user.role}),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=AccessToken)
async def refresh(payload: TokenRefreshRequest, db: DbSession):
    try:
        data = decode_token(payload.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    if data.get("type") != TOKEN_TYPE_REFRESH:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token")
    user = (
        await db.execute(select(User).where(User.id == int(data["sub"])))
    ).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return AccessToken(access_token=create_access_token(user.id, extra={"role": user.role}))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
