from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_type: Literal["topside", "marine"]
    code: str | None = Field(default=None, max_length=64)
    description: str | None = None
    client: str | None = None
    facility: str | None = None
    location: str | None = None
    # OneDrive root: either an item_id or an absolute path within the drive.
    onedrive_root_path: str | None = None
    onedrive_drive_id: str | None = None
    onedrive_root_item_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    client: str | None = None
    facility: str | None = None
    location: str | None = None
    onedrive_root_path: str | None = None
    onedrive_drive_id: str | None = None
    onedrive_root_item_id: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    project_type: str
    description: str | None
    client: str | None
    facility: str | None
    location: str | None
    onedrive_root_path: str | None
    onedrive_drive_id: str | None
    onedrive_root_item_id: str | None
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


class ProjectMemberAdd(BaseModel):
    user_id: int
    role: Literal["viewer", "editor", "admin"] = "viewer"


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    user_id: int
    role: str
