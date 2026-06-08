"""Admin database browser, mounted at ``/admin``.

Built on sqladmin — auto-generates list / view / edit / delete views from
the SQLAlchemy models so you can inspect (and carefully edit) every table
without psql. Authentication piggybacks on the existing User model: only
``is_superuser`` accounts can sign in to the admin (regular users can't,
even with valid credentials, so the admin isn't reachable by anyone with
a normal project login).

Mounted in ``main.py`` via :func:`setup_admin`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse

from wtforms import PasswordField
from wtforms.validators import Length, Optional as OptionalValidator

from app.config import settings
from app.core.security import hash_password, verify_password
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AuditLog,
    Equipment,
    EquipmentVersion,
    FileExtraction,
    OneDriveToken,
    Project,
    ProjectFile,
    ProjectMember,
    ProjectOneDriveSelection,
    User,
)


# ---------------------------------------------------------------------------
# Authentication backend — short-lived signed cookie
# ---------------------------------------------------------------------------

class AdminAuth(AuthenticationBackend):
    """Cookie-based admin auth. Reuses the project's User table + bcrypt
    password hashing. The session token is a JWT (HS256) signed with the
    same ``JWT_SECRET_KEY`` the API uses, so we don't introduce a second
    secret. Token claim ``adm=True`` distinguishes admin sessions from
    regular API tokens — a regular user JWT can't be used to sign in
    here, and vice versa.
    """

    SESSION_KEY = "mel_admin"
    TTL_HOURS = 12

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = (form.get("username") or "").strip().lower()
        password = form.get("password") or ""
        if not email or not password:
            return False

        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()

        if not user or not user.is_active:
            return False
        if not user.is_superuser:
            # Plain users — even if their password is right — can't reach
            # the admin. This is the gate.
            return False
        if not verify_password(password, user.hashed_password):
            return False

        token = jwt.encode(
            {
                "sub": str(user.id),
                "adm": True,
                "exp": datetime.now(timezone.utc) + timedelta(hours=self.TTL_HOURS),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        request.session.update({self.SESSION_KEY: token})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.pop(self.SESSION_KEY, None)
        return True

    async def authenticate(self, request: Request) -> bool | RedirectResponse:
        token = request.session.get(self.SESSION_KEY)
        if not token:
            return False
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except JWTError:
            return False
        if not payload.get("adm"):
            return False
        # Re-check the user is still active + still a superuser. Cheap on
        # every page load, but it means revoking superuser actually kicks
        # the active admin session.
        try:
            uid = int(payload.get("sub"))
        except (TypeError, ValueError):
            return False
        async with AsyncSessionLocal() as db:
            user = (
                await db.execute(select(User).where(User.id == uid))
            ).scalar_one_or_none()
        if not user or not user.is_active or not user.is_superuser:
            return False
        return True


# ---------------------------------------------------------------------------
# Model views — one per table, with sensible columns
# ---------------------------------------------------------------------------

class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"
    column_list = (
        User.id, User.email, User.full_name, User.role,
        User.is_active, User.is_superuser, User.created_at,
    )
    column_searchable_list = (User.email, User.full_name)
    column_sortable_list = (User.id, User.email, User.created_at)
    column_default_sort = [("id", False)]
    # Hide the raw bcrypt column from the form. We inject a plaintext
    # "New password" field below and hash it on save.
    form_excluded_columns = ("hashed_password", "created_at", "updated_at")

    async def scaffold_form(self, rules=None):
        form_class = await super().scaffold_form(rules)
        # Edit form: empty = keep current password.
        # Create form: empty = require a password (validated in on_model_change).
        form_class.new_password = PasswordField(
            "New password",
            description="Leave blank to keep the current password unchanged. Minimum 8 characters.",
            validators=[OptionalValidator(), Length(min=8, max=128)],
        )
        return form_class

    async def on_model_change(self, data, model, is_created, request):
        # Pop the virtual field so SQLAlchemy doesn't see it as a column.
        new_pw = (data.pop("new_password", "") or "").strip()
        if new_pw:
            model.hashed_password = hash_password(new_pw)
        elif is_created:
            # Creating a brand-new user without a password would leave the
            # NOT NULL ``hashed_password`` column unset → DB error. Block it
            # here with a friendly message instead of a stack trace.
            raise ValueError(
                "A password is required when creating a new user. "
                "Enter one in the 'New password' field."
            )
        # else (editing an existing user, password left blank): keep the
        # existing hashed_password as-is.
        return await super().on_model_change(data, model, is_created, request)


class ProjectAdmin(ModelView, model=Project):
    name = "Project"
    name_plural = "Projects"
    icon = "fa-solid fa-briefcase"
    column_list = (
        Project.id, Project.name, Project.code, Project.project_type,
        Project.client, Project.facility, Project.location,
        Project.topside_onedrive_root_path, Project.marine_onedrive_root_path,
        Project.created_at,
    )
    column_searchable_list = (Project.name, Project.code, Project.client)
    column_sortable_list = (Project.id, Project.name, Project.created_at)
    column_default_sort = [("id", False)]


class ProjectMemberAdmin(ModelView, model=ProjectMember):
    name = "Project Member"
    name_plural = "Project Members"
    icon = "fa-solid fa-users"
    column_list = (ProjectMember.id, ProjectMember.project_id, ProjectMember.user_id, ProjectMember.role)
    column_sortable_list = (ProjectMember.id, ProjectMember.project_id)


class EquipmentAdmin(ModelView, model=Equipment):
    name = "Equipment"
    name_plural = "Equipment"
    icon = "fa-solid fa-wrench"
    column_list = (
        Equipment.id, Equipment.project_id, Equipment.workspace,
        Equipment.client_tag, Equipment.description, Equipment.module,
        Equipment.equipment_type, Equipment.vendor,
        Equipment.current_version, Equipment.last_source,
        Equipment.updated_at,
    )
    column_searchable_list = (
        Equipment.client_tag, Equipment.old_tag, Equipment.description, Equipment.vendor,
    )
    column_sortable_list = (
        Equipment.id, Equipment.client_tag, Equipment.module,
        Equipment.current_version, Equipment.updated_at,
    )
    column_default_sort = [("id", False)]
    page_size = 50
    page_size_options = [25, 50, 100, 200]


class EquipmentVersionAdmin(ModelView, model=EquipmentVersion):
    name = "Equipment Version"
    name_plural = "Equipment Versions"
    icon = "fa-solid fa-clock-rotate-left"
    column_list = (
        EquipmentVersion.id, EquipmentVersion.equipment_id, EquipmentVersion.version_no,
        EquipmentVersion.source, EquipmentVersion.source_file_id,
        EquipmentVersion.created_by_id, EquipmentVersion.created_at,
    )
    column_sortable_list = (
        EquipmentVersion.id, EquipmentVersion.equipment_id,
        EquipmentVersion.version_no, EquipmentVersion.created_at,
    )
    column_default_sort = [("id", True)]


class ProjectFileAdmin(ModelView, model=ProjectFile):
    name = "Project File"
    name_plural = "Project Files"
    icon = "fa-solid fa-file"
    column_list = (
        ProjectFile.id, ProjectFile.project_id, ProjectFile.workspace,
        ProjectFile.name, ProjectFile.folder_category, ProjectFile.extension,
        ProjectFile.size_bytes, ProjectFile.sync_status,
        ProjectFile.last_synced_at,
    )
    column_searchable_list = (ProjectFile.name, ProjectFile.onedrive_path)
    column_sortable_list = (
        ProjectFile.id, ProjectFile.name, ProjectFile.last_synced_at,
    )
    column_default_sort = [("id", True)]


class FileExtractionAdmin(ModelView, model=FileExtraction):
    name = "File Extraction"
    name_plural = "File Extractions"
    icon = "fa-solid fa-microscope"
    column_list = (
        FileExtraction.id, FileExtraction.file_id, FileExtraction.parser,
        FileExtraction.status, FileExtraction.pages, FileExtraction.used_ocr,
        FileExtraction.created_at,
    )
    column_sortable_list = (FileExtraction.id, FileExtraction.created_at)
    column_default_sort = [("id", True)]
    # data is huge JSON — don't show in list, only on detail
    form_excluded_columns = ("data",)


class ProjectOneDriveSelectionAdmin(ModelView, model=ProjectOneDriveSelection):
    name = "OneDrive Selection"
    name_plural = "OneDrive Selections"
    icon = "fa-solid fa-cloud"
    column_list = (
        ProjectOneDriveSelection.id, ProjectOneDriveSelection.project_id,
        ProjectOneDriveSelection.workspace, ProjectOneDriveSelection.name,
        ProjectOneDriveSelection.item_type, ProjectOneDriveSelection.item_path,
        ProjectOneDriveSelection.size_bytes,
    )
    column_searchable_list = (
        ProjectOneDriveSelection.name, ProjectOneDriveSelection.item_path,
    )


class OneDriveTokenAdmin(ModelView, model=OneDriveToken):
    name = "OneDrive Token"
    name_plural = "OneDrive Tokens"
    icon = "fa-solid fa-key"
    column_list = (
        OneDriveToken.id, OneDriveToken.account_email, OneDriveToken.tenant_id,
        OneDriveToken.expires_at, OneDriveToken.created_at,
    )
    # Don't expose raw tokens on the list view
    column_details_exclude_list = ("access_token", "refresh_token")


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Audit Log"
    name_plural = "Audit Log"
    icon = "fa-solid fa-list-check"
    column_list = (
        AuditLog.id, AuditLog.action, AuditLog.user_id, AuditLog.project_id,
        AuditLog.entity_type, AuditLog.entity_id, AuditLog.created_at,
    )
    column_searchable_list = (AuditLog.action, AuditLog.entity_type)
    column_sortable_list = (AuditLog.id, AuditLog.created_at)
    column_default_sort = [("id", True)]
    # Audit log is append-only; disable mutating operations from the UI
    can_create = False
    can_edit = False
    can_delete = False


# ---------------------------------------------------------------------------
# Public setup helper — called from main.py
# ---------------------------------------------------------------------------

def setup_admin(app) -> Admin:
    """Mount the admin at ``/admin``. Returns the Admin instance in case
    callers want to register more views.
    """
    admin = Admin(
        app=app,
        engine=engine,
        title=f"{settings.APP_NAME} · Admin",
        authentication_backend=AdminAuth(secret_key=settings.JWT_SECRET_KEY),
        base_url="/admin",
    )

    admin.add_view(UserAdmin)
    admin.add_view(ProjectAdmin)
    admin.add_view(ProjectMemberAdmin)
    admin.add_view(EquipmentAdmin)
    admin.add_view(EquipmentVersionAdmin)
    admin.add_view(ProjectFileAdmin)
    admin.add_view(FileExtractionAdmin)
    admin.add_view(ProjectOneDriveSelectionAdmin)
    admin.add_view(OneDriveTokenAdmin)
    admin.add_view(AuditLogAdmin)

    return admin
