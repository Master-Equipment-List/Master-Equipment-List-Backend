from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def log(
    db: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    project_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        user_id=user_id,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_=metadata or {},
    )
    db.add(entry)
    await db.flush()
    return entry
