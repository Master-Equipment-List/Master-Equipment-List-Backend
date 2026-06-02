"""Bootstrap the first admin user. Idempotent."""
import asyncio
import sys

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models import User


async def main() -> None:
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.email == settings.FIRST_ADMIN_EMAIL))
        ).scalar_one_or_none()
        if existing:
            print(f"Admin already exists: {existing.email}")
            return

        user = User(
            email=settings.FIRST_ADMIN_EMAIL,
            full_name=settings.FIRST_ADMIN_NAME,
            hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
            role="admin",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        await db.commit()
        print(f"Created admin: {user.email}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
