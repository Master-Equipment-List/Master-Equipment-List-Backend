from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class OneDriveToken(Base, TimestampMixin):
    """Organization-level OneDrive OAuth tokens.

    A single org-wide service identity (admin consent) is stored here.
    Project access is enforced by the configured root path on the Project row,
    not by per-user OneDrive tokens.
    """

    __tablename__ = "onedrive_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectOneDriveSelection(Base, TimestampMixin):
    """Files / folders selected by the user for sync within a project's root."""

    __tablename__ = "project_onedrive_selections"
    __table_args__ = (
        UniqueConstraint("project_id", "item_id", name="uq_project_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    item_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)  # file | folder
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
