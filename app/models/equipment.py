from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Equipment(Base, TimestampMixin):
    """One row per equipment item in a project's MEL.

    The structured fields are denormalized for fast filtering and Excel export.
    `data` holds the full JSON record (raw + extras) for forward compatibility.
    """

    __tablename__ = "equipment"
    __table_args__ = (
        UniqueConstraint("project_id", "client_tag", name="uq_equipment_project_tag"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    # Identification
    rev_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_tag: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    client_tag: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    equipment_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    design_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    material: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Process — kept as free-form text because real MEL data routinely has
    # multi-line cells with notes ("Inlet: x / Permeate: y", "during CIP ...").
    operating_press: Mapped[str | None] = mapped_column(Text, nullable=True)
    operating_temp: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_press: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_temp: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_flow: Mapped[str | None] = mapped_column(Text, nullable=True)
    pump_capacity: Mapped[str | None] = mapped_column(Text, nullable=True)
    heat_exchanger_duty_kw: Mapped[str | None] = mapped_column(Text, nullable=True)
    liquid_fill: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Vendor-updatable fields (target of Vendor Data extraction)
    absorbed_power_kw: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_power_kw: Mapped[str | None] = mapped_column(Text, nullable=True)
    length_m: Mapped[str | None] = mapped_column(Text, nullable=True)
    width_id_m: Mapped[str | None] = mapped_column(Text, nullable=True)
    height_tt_m: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_weight_mt: Mapped[str | None] = mapped_column(Text, nullable=True)
    operating_weight_mt: Mapped[str | None] = mapped_column(Text, nullable=True)
    hydrotest_weight_mt: Mapped[str | None] = mapped_column(Text, nullable=True)

    pid: Mapped[str | None] = mapped_column(Text, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_dry_weight_mt: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_operating_weight_mt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full record + any extras
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Bookkeeping
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True
    )
    last_updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list["EquipmentVersion"]] = relationship(
        back_populates="equipment",
        cascade="all, delete-orphan",
        order_by="EquipmentVersion.version_no",
    )


class EquipmentVersion(Base, TimestampMixin):
    """Immutable snapshot taken every time an Equipment row is updated."""

    __tablename__ = "equipment_versions"
    __table_args__ = (
        UniqueConstraint("equipment_id", "version_no", name="uq_equipment_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # snapshot of the Equipment row's full payload (data + denormalized fields)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    # which fields were touched in this version compared to the prior one
    changed_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # manual | pfd | vendor | excel | seed
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    equipment: Mapped[Equipment] = relationship(back_populates="versions")
