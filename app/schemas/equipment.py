from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EquipmentBase(BaseModel):
    workspace: str = "topside"  # "topside" | "marine"
    rev_no: str | None = None
    old_tag: str | None = None
    client_tag: str
    description: str | None = None
    vendor: str | None = None
    equipment_type: str | None = None
    module: str | None = None
    design_code: str | None = None
    orientation: str | None = None
    material: str | None = None
    configuration: str | None = None
    location: str | None = None
    operating_press: str | None = None
    operating_temp: str | None = None
    design_press: str | None = None
    design_temp: str | None = None
    design_flow: str | None = None
    pump_capacity: str | None = None
    heat_exchanger_duty_kw: str | None = None
    liquid_fill: str | None = None
    absorbed_power_kw: str | None = None
    rated_power_kw: str | None = None
    length_m: str | None = None
    width_id_m: str | None = None
    height_tt_m: str | None = None
    dry_weight_mt: str | None = None
    operating_weight_mt: str | None = None
    hydrotest_weight_mt: str | None = None
    pid: str | None = None
    remarks: str | None = None
    total_dry_weight_mt: str | None = None
    total_operating_weight_mt: str | None = None
    lifecycle_status: str | None = None


class EquipmentCreate(EquipmentBase):
    data: dict[str, Any] = Field(default_factory=dict)


class EquipmentUpdate(BaseModel):
    rev_no: str | None = None
    old_tag: str | None = None
    description: str | None = None
    vendor: str | None = None
    equipment_type: str | None = None
    module: str | None = None
    design_code: str | None = None
    orientation: str | None = None
    material: str | None = None
    configuration: str | None = None
    location: str | None = None
    operating_press: str | None = None
    operating_temp: str | None = None
    design_press: str | None = None
    design_temp: str | None = None
    design_flow: str | None = None
    pump_capacity: str | None = None
    heat_exchanger_duty_kw: str | None = None
    liquid_fill: str | None = None
    absorbed_power_kw: str | None = None
    rated_power_kw: str | None = None
    length_m: str | None = None
    width_id_m: str | None = None
    height_tt_m: str | None = None
    dry_weight_mt: str | None = None
    operating_weight_mt: str | None = None
    hydrotest_weight_mt: str | None = None
    pid: str | None = None
    remarks: str | None = None
    total_dry_weight_mt: str | None = None
    total_operating_weight_mt: str | None = None
    lifecycle_status: str | None = None
    data: dict[str, Any] | None = None
    note: str | None = None


class EquipmentOut(EquipmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    data: dict[str, Any]
    current_version: int
    last_source: str | None
    last_source_file_id: int | None
    last_updated_by_id: int | None
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


class EquipmentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    equipment_id: int
    version_no: int
    snapshot: dict[str, Any]
    changed_fields: list[str]
    source: str
    source_file_id: int | None
    note: str | None
    created_by_id: int | None
    created_at: datetime


class EquipmentDiff(BaseModel):
    equipment_id: int
    from_version: int
    to_version: int
    fields: dict[str, dict[str, Any]]  # field -> {from, to}
