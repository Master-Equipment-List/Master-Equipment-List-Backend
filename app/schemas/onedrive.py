from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DriveItem(BaseModel):
    id: str
    name: str
    path: str
    type: Literal["file", "folder"]
    size: int | None = None
    modified_at: datetime | None = None
    mime_type: str | None = None
    # Shortcut metadata (when the item is a `remoteItem`, e.g. a folder
    # shared with the connected account and added as a shortcut).
    is_shortcut: bool = False
    remote_item_id: str | None = None
    remote_drive_id: str | None = None


class BrowseResponse(BaseModel):
    project_id: int
    root_path: str | None
    items: list[DriveItem]
    total: int
    limit: int
    offset: int


class SelectionItem(BaseModel):
    item_id: str
    item_path: str
    item_type: Literal["file", "folder"]
    name: str
    size_bytes: int | None = None


class SelectionRequest(BaseModel):
    items: list[SelectionItem]
    replace: bool = True


class SelectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    item_id: str
    item_path: str
    item_type: str
    name: str
    size_bytes: int | None


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str
