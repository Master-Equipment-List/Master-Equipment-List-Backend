from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# asyncpg needs SSL passed as a driver kwarg, not via the URL string.
# Hosted Postgres (Render, Heroku, RDS) requires it; localhost rarely does.
_connect_args: dict[str, object] = {}
if settings.DB_SSL:
    _connect_args["ssl"] = True

engine = create_async_engine(
    settings.database_url,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
