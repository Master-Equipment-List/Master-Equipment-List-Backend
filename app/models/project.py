from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

PROJECT_TYPES = ("topside", "marine")
PROJECT_ROLES = ("viewer", "editor", "admin")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    project_type: Mapped[str] = mapped_column(
        SAEnum(*PROJECT_TYPES, name="project_type_enum"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    facility: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Legacy single-root OneDrive columns — kept for backward compatibility.
    # New flow uses the per-workspace columns below (topside_*, marine_*).
    onedrive_drive_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onedrive_root_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onedrive_root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Per-workspace OneDrive roots. Topside and Marine each have their own
    # so a single project can host two independent sync targets.
    topside_onedrive_drive_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topside_onedrive_root_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topside_onedrive_root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    marine_onedrive_drive_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marine_onedrive_root_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marine_onedrive_root_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def onedrive_root_for(self, workspace: str) -> tuple[str | None, str | None, str | None]:
        """Resolve (root_path, root_item_id, drive_id) for a workspace.

        Falls back to the legacy single-root columns when the workspace-
        specific ones haven't been set yet — this keeps existing projects
        working while users gradually configure separate Topside and Marine
        roots.
        """
        if workspace == "marine":
            return (
                self.marine_onedrive_root_path or None,
                self.marine_onedrive_root_item_id or None,
                self.marine_onedrive_drive_id or None,
            )
        # Default to topside for any other value (incl. unknown — safer
        # than raising mid-request).
        return (
            self.topside_onedrive_root_path or self.onedrive_root_path or None,
            self.topside_onedrive_root_item_id or self.onedrive_root_item_id or None,
            self.topside_onedrive_drive_id or self.onedrive_drive_id or None,
        )


class ProjectMember(Base, TimestampMixin):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(
        SAEnum(*PROJECT_ROLES, name="project_role_enum"), nullable=False, default="viewer"
    )

    project: Mapped[Project] = relationship(back_populates="members")
