from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    onedrive_path: str
    folder_category: str | None
    mime_type: str | None
    extension: str | None
    size_bytes: int | None
    onedrive_modified_at: datetime | None
    last_synced_at: datetime | None
    sync_status: str
    sync_error: str | None


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    parser: str
    status: str
    error: str | None
    pages: int | None
    used_ocr: bool
    data: dict[str, Any]
    created_at: datetime


class FileWithExtractionOut(FileOut):
    extractions: list[ExtractionOut] = []
