"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    project_type = sa.Enum("topside", "marine", name="project_type_enum")
    project_role = sa.Enum("viewer", "editor", "admin", name="project_role_enum")

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(64), unique=True, nullable=True),
        sa.Column("project_type", project_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client", sa.String(255), nullable=True),
        sa.Column("facility", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("onedrive_drive_id", sa.String(255), nullable=True),
        sa.Column("onedrive_root_item_id", sa.String(255), nullable=True),
        sa.Column("onedrive_root_path", sa.String(1024), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", project_role, nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_user"),
    )

    op.create_table(
        "onedrive_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, unique=True),
        sa.Column("account_email", sa.String(255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(32), nullable=False, server_default="Bearer"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "project_onedrive_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.String(255), nullable=False),
        sa.Column("item_path", sa.String(1024), nullable=False),
        sa.Column("item_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "item_id", name="uq_project_item"),
    )

    op.create_table(
        "project_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("onedrive_item_id", sa.String(255), nullable=False),
        sa.Column("onedrive_path", sa.String(1024), nullable=False),
        sa.Column("folder_category", sa.String(128), nullable=True),
        sa.Column("mime_type", sa.String(255), nullable=True),
        sa.Column("extension", sa.String(32), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("onedrive_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("local_path", sa.String(1024), nullable=True),
        sa.Column("sync_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "onedrive_item_id", name="uq_projfile_drive_item"),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])
    op.create_index("ix_project_files_name", "project_files", ["name"])
    op.create_index("ix_project_files_folder_category", "project_files", ["folder_category"])
    op.create_index("ix_project_files_extension", "project_files", ["extension"])
    op.create_index("ix_project_files_onedrive_item_id", "project_files", ["onedrive_item_id"])

    op.create_table(
        "file_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("project_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parser", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="success"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("used_ocr", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_file_extractions_file_id", "file_extractions", ["file_id"])

    op.create_table(
        "equipment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rev_no", sa.Text(), nullable=True),
        sa.Column("old_tag", sa.String(255), nullable=True),
        sa.Column("client_tag", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("vendor", sa.String(255), nullable=True),
        sa.Column("equipment_type", sa.String(255), nullable=True),
        sa.Column("module", sa.String(128), nullable=True),
        sa.Column("design_code", sa.Text(), nullable=True),
        sa.Column("orientation", sa.String(64), nullable=True),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("configuration", sa.String(64), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("operating_press", sa.Text(), nullable=True),
        sa.Column("operating_temp", sa.Text(), nullable=True),
        sa.Column("design_press", sa.Text(), nullable=True),
        sa.Column("design_temp", sa.Text(), nullable=True),
        sa.Column("design_flow", sa.Text(), nullable=True),
        sa.Column("pump_capacity", sa.Text(), nullable=True),
        sa.Column("heat_exchanger_duty_kw", sa.Text(), nullable=True),
        sa.Column("liquid_fill", sa.Text(), nullable=True),
        sa.Column("absorbed_power_kw", sa.Text(), nullable=True),
        sa.Column("rated_power_kw", sa.Text(), nullable=True),
        sa.Column("length_m", sa.Text(), nullable=True),
        sa.Column("width_id_m", sa.Text(), nullable=True),
        sa.Column("height_tt_m", sa.Text(), nullable=True),
        sa.Column("dry_weight_mt", sa.Text(), nullable=True),
        sa.Column("operating_weight_mt", sa.Text(), nullable=True),
        sa.Column("hydrotest_weight_mt", sa.Text(), nullable=True),
        sa.Column("pid", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("total_dry_weight_mt", sa.Text(), nullable=True),
        sa.Column("total_operating_weight_mt", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_source", sa.String(32), nullable=True),
        sa.Column("last_source_file_id", sa.Integer(), sa.ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "client_tag", name="uq_equipment_project_tag"),
    )
    op.create_index("ix_equipment_project_id", "equipment", ["project_id"])
    op.create_index("ix_equipment_client_tag", "equipment", ["client_tag"])
    op.create_index("ix_equipment_old_tag", "equipment", ["old_tag"])

    op.create_table(
        "equipment_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_file_id", sa.Integer(), sa.ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("equipment_id", "version_no", name="uq_equipment_version"),
    )
    op.create_index("ix_equipment_versions_equipment_id", "equipment_versions", ["equipment_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("equipment_versions")
    op.drop_table("equipment")
    op.drop_table("file_extractions")
    op.drop_table("project_files")
    op.drop_table("project_onedrive_selections")
    op.drop_table("onedrive_tokens")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    sa.Enum(name="project_role_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="project_type_enum").drop(op.get_bind(), checkfirst=True)
