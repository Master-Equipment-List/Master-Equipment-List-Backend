from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PendingChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    equipment_id: int
    client_tag: str
    description: str | None
    workspace: str
    source: str
    source_file_id: int | None
    source_file_name: str | None
    # {"field_name": {"old": ..., "new": ...}, ...}
    proposed_fields: dict[str, dict[str, Any]]
    # "pending" | "approved" | "rejected"
    status: str
    created_by_name: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovePendingChangeRequest(BaseModel):
    # Field names from `proposed_fields` to actually apply (accept the
    # "new" value). Any proposed field NOT listed here keeps its existing
    # value untouched.
    accepted_fields: list[str] = []


class ResolvePendingChangeResponse(BaseModel):
    applied_fields: list[str] = []
