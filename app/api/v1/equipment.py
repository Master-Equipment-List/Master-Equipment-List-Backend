import io
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession, project_access
from app.extractors.topside_excel import extract_equipment_rows
from app.models import Equipment, Project
from app.schemas.equipment import EquipmentCreate, EquipmentOut, EquipmentUpdate
from app.services import audit_service
from app.services.version_service import apply_update, record_initial_version

router = APIRouter()


@router.get("/projects/{project_id}/equipment", response_model=list[EquipmentOut])
async def list_equipment(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
    q: str | None = Query(None, description="Search by client tag, old tag, or description."),
    module: str | None = None,
    equipment_type: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    stmt = select(Equipment).where(Equipment.project_id == project.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Equipment.client_tag.ilike(like))
            | (Equipment.old_tag.ilike(like))
            | (Equipment.description.ilike(like))
        )
    if module:
        stmt = stmt.where(Equipment.module == module)
    if equipment_type:
        stmt = stmt.where(Equipment.equipment_type == equipment_type)
    stmt = stmt.order_by(Equipment.client_tag).limit(limit).offset(offset)
    return (await db.execute(stmt)).scalars().all()


@router.post("/projects/{project_id}/equipment", response_model=EquipmentOut, status_code=201)
async def create_equipment(
    payload: EquipmentCreate,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    data = payload.model_dump()
    extras = data.pop("data", {})
    eq = Equipment(project_id=project.id, data=extras, created_by_id=user.id, **data)
    db.add(eq)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="client_tag already exists for this project")
    await record_initial_version(db, eq, source="manual", user_id=user.id)
    await audit_service.log(
        db, action="equipment.create",
        user_id=user.id, project_id=project.id,
        entity_type="equipment", entity_id=eq.id,
    )
    await db.commit()
    await db.refresh(eq)
    return eq


@router.get("/projects/{project_id}/equipment/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(
    equipment_id: int,
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return eq


@router.patch("/projects/{project_id}/equipment/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: int,
    payload: EquipmentUpdate,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")

    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    extras: dict[str, Any] | None = data.pop("data", None)
    await apply_update(
        db, eq, data,
        source="manual",
        source_file_id=None,
        user_id=user.id,
        note=note,
        extra_data=extras,
    )
    await audit_service.log(
        db, action="equipment.update",
        user_id=user.id, project_id=project.id,
        entity_type="equipment", entity_id=eq.id,
        metadata={"changes": list(data.keys())},
    )
    await db.commit()
    await db.refresh(eq)
    return eq


@router.delete("/projects/{project_id}/equipment/{equipment_id}", status_code=204)
async def delete_equipment(
    equipment_id: int,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    eq = (
        await db.execute(
            select(Equipment).where(
                Equipment.id == equipment_id, Equipment.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipment not found")
    await audit_service.log(
        db, action="equipment.delete",
        user_id=user.id, project_id=project.id,
        entity_type="equipment", entity_id=eq.id,
    )
    await db.delete(eq)
    await db.commit()


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=5000)


@router.post("/projects/{project_id}/equipment/bulk-delete")
async def bulk_delete_equipment(
    payload: BulkDeleteRequest,
    db: DbSession,
    user: CurrentUser,
    project: Project = Depends(project_access("editor")),
):
    """Delete many equipment rows in one transaction.

    Only rows that actually belong to ``project_id`` are deleted — IDs
    from other projects (or non-existent IDs) are silently ignored, so a
    stale UI never wipes the wrong rows. Returns counts the caller can
    show in a toast: ``deleted`` (actual rows removed) + ``not_found``
    (ids the user asked about that weren't in this project).
    """
    if not payload.ids:
        return {"deleted": 0, "not_found": 0}

    rows = (
        await db.execute(
            select(Equipment).where(
                Equipment.id.in_(payload.ids),
                Equipment.project_id == project.id,
            )
        )
    ).scalars().all()
    found_ids = {r.id for r in rows}
    not_found = len([i for i in payload.ids if i not in found_ids])

    for r in rows:
        await db.delete(r)

    await audit_service.log(
        db,
        action="equipment.bulk_delete",
        user_id=user.id,
        project_id=project.id,
        metadata={
            "deleted": len(found_ids),
            "not_found": not_found,
            "ids": list(found_ids),
        },
    )
    await db.commit()
    return {"deleted": len(found_ids), "not_found": not_found}


# --- Excel export matching the Topside template ---

EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("rev_no", "REV No."),
    ("old_tag", "OLD EQUIPMENT / TAG No."),
    ("client_tag", "CLIENT EQUIPMENT TAG"),
    ("description", "DESCRIPTION"),
    ("vendor", "VENDOR"),
    ("equipment_type", "EQUIPMENT TYPE"),
    ("module", "MODULE"),
    ("design_code", "EQUIPMENT DESIGN CODE/CLASS"),
    ("orientation", "ORIENTATION"),
    ("material", "MATERIAL OF CONSTRUCTION"),
    ("configuration", "CONFIGURATION"),
    ("location", "LOCATION"),
    ("operating_press", "OPERATING PRESS (barg)"),
    ("operating_temp", "OPERATING TEMP (oC)"),
    ("design_press", "DESIGN PRESS (barg)"),
    ("design_temp", "DESIGN TEMP (oC)"),
    ("design_flow", "DESIGN FLOW m3/hr"),
    ("pump_capacity", "PUMP / COMPRESSOR / TANK CAPACITY"),
    ("heat_exchanger_duty_kw", "HEAT EXCHANGER DUTY (kW)"),
    ("liquid_fill", "LIQUID FILL"),
    ("absorbed_power_kw", "ABSORBED POWER PER UNIT (kW)"),
    ("rated_power_kw", "RATED POWER PER UNIT (kW)"),
    ("length_m", "L or T/T (m)"),
    ("width_id_m", "W or I.D (m)"),
    ("height_tt_m", "H or T/T (m)"),
    ("dry_weight_mt", "DRY WT in MT"),
    ("operating_weight_mt", "OPE WT in MT"),
    ("hydrotest_weight_mt", "HYDROTEST WT in MT"),
    ("pid", "P&ID"),
    ("remarks", "REMARKS"),
    ("total_dry_weight_mt", "TOTAL DRY WT in MT"),
    ("total_operating_weight_mt", "TOTAL OPE WT in MT"),
]


@router.get("/projects/{project_id}/export/excel")
async def export_excel(
    db: DbSession,
    project: Project = Depends(project_access("viewer")),
):
    rows = (
        await db.execute(
            select(Equipment).where(Equipment.project_id == project.id).order_by(Equipment.client_tag)
        )
    ).scalars().all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "EQUIPMENT LIST"

    # Header banner
    ws.append([f"{project.project_type.upper()} EQUIPMENT LIST"])
    ws.append([f"Project: {project.name}"])
    ws.append([f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z"])
    ws.append([])
    ws.append([h for _, h in EXPORT_COLUMNS])
    for eq in rows:
        ws.append([getattr(eq, attr) for attr, _ in EXPORT_COLUMNS])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{project.code or 'project'}_{project.project_type}_equipment_list.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --- Bulk import from Excel ---

# Fields that can be set from a parsed Excel row (mirrors EquipmentCreate).
_IMPORTABLE_FIELDS = [
    "rev_no", "old_tag", "client_tag", "description", "vendor", "equipment_type",
    "module", "design_code", "orientation", "material", "configuration", "location",
    "operating_press", "operating_temp", "design_press", "design_temp", "design_flow",
    "pump_capacity", "heat_exchanger_duty_kw", "liquid_fill",
    "absorbed_power_kw", "rated_power_kw",
    "length_m", "width_id_m", "height_tt_m",
    "dry_weight_mt", "operating_weight_mt", "hydrotest_weight_mt",
    "pid", "remarks", "total_dry_weight_mt", "total_operating_weight_mt",
]


@router.post("/projects/{project_id}/equipment/import")
async def import_equipment_excel(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="Equipment List .xlsx file"),
    sheet_name: str | None = Query(None, description="Override sheet to read; default = 'EQUIPMENT LIST' or the largest sheet."),
    commit: bool = Query(False, description="If false, return a parse preview without writing to the database."),
    mode: str = Query(
        "skip_existing",
        regex="^(skip_existing|update_existing)$",
        description="Conflict policy when commit=true: skip_existing leaves matched rows alone; update_existing PATCHes them.",
    ),
    project: Project = Depends(project_access("editor")),
):
    """Bulk-import equipment from a Topside-Equipment-List style Excel file.

    Same parser as scripts/seed_topside_poc.py — locates the EQUIPMENT row
    header by content patterns, builds a column map, then walks the data rows.

    With `commit=false` (default) the response is a preview only; the client
    can show the user what would be imported, then re-POST with `commit=true`.
    """
    # Persist the upload to a tempfile because openpyxl needs a real path.
    suffix = Path(file.filename or "").suffix.lower() or ".xlsx"
    if suffix not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="Only .xlsx / .xlsm are supported")

    body = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name

    try:
        try:
            parsed = extract_equipment_rows(tmp_path, sheet_name=sheet_name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to parse Excel: {e}")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    if not parsed:
        raise HTTPException(
            status_code=400,
            detail="No equipment rows recognized in the file. Make sure the sheet has a 'CLIENT EQUIPMENT TAG' header.",
        )

    # Look up existing client_tags so we can mark new vs existing.
    existing_rows = (
        await db.execute(select(Equipment).where(Equipment.project_id == project.id))
    ).scalars().all()
    existing_by_tag = {r.client_tag: r for r in existing_rows}

    preview: list[dict[str, Any]] = []
    for i, row in enumerate(parsed, start=1):
        tag = (row.get("client_tag") or "").strip()
        if not tag:
            preview.append({
                "row_number": i, "client_tag": None,
                "status": "invalid", "reason": "missing client_tag",
            })
            continue
        is_existing = tag in existing_by_tag
        clean: dict[str, Any] = {}
        for k in _IMPORTABLE_FIELDS:
            v = row.get(k)
            if isinstance(v, str):
                v = v.strip()
                if not v or v == "-":
                    v = None
            clean[k] = v
        preview.append({
            "row_number": i,
            "client_tag": tag,
            "status": "existing" if is_existing else "new",
            "fields": clean,
            "raw_extra": row.get("__raw") or {},
        })

    summary = {
        "total_rows": len(parsed),
        "new": sum(1 for p in preview if p["status"] == "new"),
        "existing": sum(1 for p in preview if p["status"] == "existing"),
        "invalid": sum(1 for p in preview if p["status"] == "invalid"),
        "commit": commit,
        "mode": mode,
    }

    if not commit:
        # Return preview only — first 200 rows to keep response sane
        return {
            **summary,
            "preview": preview[:200],
            "preview_truncated": len(preview) > 200,
        }

    # Commit path. Each row is wrapped in a SAVEPOINT so one row's failure
    # (duplicate, validation error, etc.) doesn't roll back previously-saved
    # rows or leave the session in an unusable state.
    created = 0
    updated = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for p in preview:
        if p["status"] == "invalid":
            skipped += 1
            continue
        tag = p["client_tag"]
        fields = p.get("fields") or {}
        raw_extra = p.get("raw_extra") or {}

        if p["status"] == "existing" and mode == "skip_existing":
            skipped += 1
            continue

        try:
            async with db.begin_nested():
                if p["status"] == "existing":
                    eq = existing_by_tag[tag]
                    changes = {
                        k: v for k, v in fields.items()
                        if k != "client_tag" and v is not None
                    }
                    v = await apply_update(
                        db, eq, changes,
                        source="excel",
                        source_file_id=None,
                        user_id=user.id,
                        note=f"Imported from {file.filename}",
                        extra_data={"raw": raw_extra} if raw_extra else None,
                    )
                    if v:
                        updated += 1
                    else:
                        skipped += 1
                else:
                    eq = Equipment(
                        project_id=project.id,
                        data={"raw": raw_extra},
                        created_by_id=user.id,
                        **fields,
                    )
                    db.add(eq)
                    await db.flush()
                    await record_initial_version(db, eq, source="excel", user_id=user.id)
                    created += 1
        except IntegrityError:
            errors.append({
                "row_number": p["row_number"],
                "tag": tag,
                "error": "duplicate client_tag",
            })
        except Exception as e:  # noqa: BLE001
            errors.append({
                "row_number": p["row_number"],
                "tag": tag,
                "error": str(e),
            })

    try:
        await audit_service.log(
            db,
            action="equipment.import_excel",
            user_id=user.id,
            project_id=project.id,
            metadata={
                "filename": file.filename,
                "total_rows": summary["total_rows"],
                "created": created,
                "updated": updated,
                "skipped": skipped,
                "errors": len(errors),
                "mode": mode,
            },
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to commit import: {e}",
        )

    return {
        **summary,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
