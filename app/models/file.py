from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ProjectFile(Base, TimestampMixin):
    """A file synced from OneDrive into a project."""

    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "onedrive_item_id", name="uq_projfile_drive_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    onedrive_item_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    onedrive_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # OneDrive folder relative to project root. Used for categorization
    # (e.g. "PFD Samples" or "Vendor Data").
    folder_category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    onedrive_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Local cached copy
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Sync state
    sync_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    extractions: Mapped[list["FileExtraction"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class FileExtraction(Base, TimestampMixin):
    """The raw structured/text data extracted from a file (one per parse run)."""

    __tablename__ = "file_extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"), index=True
    )
    parser: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_ocr: Mapped[bool] = mapped_column(default=False, nullable=False)
    # JSON payload — text + tables + per-page content. Free-form per parser.
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    file: Mapped[ProjectFile] = relationship(back_populates="extractions")
