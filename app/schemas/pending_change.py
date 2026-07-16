from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PendingChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # For kind="update": the row being updated. For kind="possible_duplicate":
    # the CANDIDATE existing row the incoming tag fuzzy-matched — client_tag
    # and description below describe that candidate, not the incoming tag.
    equipment_id: int
    client_tag: str
    description: str | None
    workspace: str
    source: str
    source_file_id: int | None
    source_file_name: str | None
    # "update" | "possible_duplicate"
    kind: str
    # Only set for kind="possible_duplicate": the incoming tag that would
    # become a new equipment row's client_tag if confirmed as new.
    new_tag: str | None
    # {"field_name": {"old": ..., "new": ...}, ...} — for possible_duplicate,
    # "old" is the candidate's current value and "new" is the incoming value.
    proposed_fields: dict[str, dict[str, Any]]
    # kind="update": "pending" | "approved" | "rejected"
    # kind="possible_duplicate": "pending" | "confirmed_new" | "confirmed_duplicate" | "rejected"
    status: str
    created_by_name: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovePendingChangeRequest(BaseModel):
    # Field names from `proposed_fields` to actually apply (accept the
    # "new" value). Any proposed field NOT listed here keeps its existing
    # value untouched. Used for kind="update" approval, and for the
    # kind="possible_duplicate" "confirm as duplicate / merge" resolution.
    accepted_fields: list[str] = []


class ResolvePendingChangeResponse(BaseModel):
    applied_fields: list[str] = []


class ConfirmNewResponse(BaseModel):
    equipment_id: int
    client_tag: str
