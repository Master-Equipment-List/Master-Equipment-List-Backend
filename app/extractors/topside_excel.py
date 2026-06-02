"""Topside Equipment List Excel importer.

Tolerant of the layout variations between the two reference workbooks
(20171-SPOG and 40801-SPE). Both have a multi-line header somewhere in
the first ~10 rows and an `EQUIPMENT LIST` sheet that holds the data.
"""
from __future__ import annotations

import re
from typing import Any

import openpyxl

# Normalized field key -> list of header substrings (case-insensitive)
# The first matching column wins.
FIELD_HEADER_PATTERNS: dict[str, list[str]] = {
    "rev_no": ["rev no", "rev no."],
    "old_tag": ["old equipment", "old tag", "old 'equipment", "old equip"],
    "client_tag": ["client equipment", "tag no.", "tag no", "client tag"],
    "description": ["description"],
    "vendor": ["vendor"],
    "equipment_type": ["equipment type", "equipment\ntype"],
    "module": ["module"],
    "design_code": ["design code", "design\ncode/class", "code/class"],
    "orientation": ["orientation"],
    "material": ["material"],
    "configuration": ["configuration", "quantity and configuration"],
    "location": ["location"],
    "operating_press": ["operating pressure", "operating press"],
    "operating_temp": ["operating temperature", "operating temp"],
    "design_press": ["design pressure", "design press"],
    "design_temp": ["design temperature", "design temp"],
    "design_flow": ["design flow"],
    "pump_capacity": ["pump / compressor", "pump/compressor", "pump capacity", "tank capac"],
    "heat_exchanger_duty_kw": ["heat exchanger duty"],
    "liquid_fill": ["liquid fill"],
    "absorbed_power_kw": ["absorbed power", "absorbed\npower"],
    "rated_power_kw": ["rated power", "rated\npower"],
    "length_m": ["l or", "l or\nt/t"],
    "width_id_m": ["w or", "w or\ni.d"],
    "height_tt_m": ["h or", "h or\nt/t"],
    "dry_weight_mt": ["dry weight per unit", "dry wt"],
    "operating_weight_mt": ["operating weight per unit", "ope wt", "op weight"],
    "hydrotest_weight_mt": ["hydrotest weight"],
    "pid": ["p&id", "pfd number", "p & id"],
    "remarks": ["remarks"],
    "total_dry_weight_mt": ["total dry wt", "total dry weight"],
    "total_operating_weight_mt": ["total ope wt", "total operating weight"],
}

TAG_RE = re.compile(r"^[A-Z]{1,3}-[A-Z0-9]{2,}", re.IGNORECASE)


def _normalize_header(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _find_header_row(ws) -> int | None:
    """Locate the row that has the most matches against FIELD_HEADER_PATTERNS."""
    best_row, best_score = None, 0
    max_search = min(ws.max_row, 20)
    for r in range(1, max_search + 1):
        score = 0
        for cell in ws[r]:
            h = _normalize_header(cell.value)
            if not h:
                continue
            for patterns in FIELD_HEADER_PATTERNS.values():
                if any(p in h for p in patterns):
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_row = r
    return best_row if best_score >= 5 else None


def _build_column_map(ws, header_row: int) -> dict[str, int]:
    col_for_field: dict[str, int] = {}
    for cell in ws[header_row]:
        h = _normalize_header(cell.value)
        if not h:
            continue
        for field, patterns in FIELD_HEADER_PATTERNS.items():
            if field in col_for_field:
                continue
            if any(p in h for p in patterns):
                col_for_field[field] = cell.column
                break
    return col_for_field


def _stringify(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _looks_like_data_row(row_values: dict[str, Any]) -> bool:
    tag = row_values.get("client_tag")
    if not tag or not isinstance(tag, str):
        return False
    return bool(TAG_RE.match(tag.strip()))


def extract_equipment_rows(path: str, sheet_name: str | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    target_sheet = sheet_name or _pick_sheet(wb)
    ws = wb[target_sheet]

    header_row = _find_header_row(ws)
    if header_row is None:
        return []

    col_map = _build_column_map(ws, header_row)
    if "client_tag" not in col_map:
        # Some sheets put the tag in "old_tag"-style column; fall back.
        if "old_tag" in col_map:
            col_map["client_tag"] = col_map["old_tag"]
        else:
            return []

    out: list[dict[str, Any]] = []
    for r in range(header_row + 1, ws.max_row + 1):
        row_data: dict[str, Any] = {}
        for field, col in col_map.items():
            row_data[field] = _stringify(ws.cell(row=r, column=col).value)
        if not _looks_like_data_row(row_data):
            continue
        row_data["client_tag"] = row_data["client_tag"].strip()
        # Preserve full row content in `data.raw` for forward compatibility
        full = {
            f"col_{c}": _stringify(ws.cell(row=r, column=c).value)
            for c in range(1, ws.max_column + 1)
        }
        row_data["__raw"] = full
        out.append(row_data)
    return out


def _pick_sheet(wb) -> str:
    for name in wb.sheetnames:
        if name.strip().lower() in ("equipment list", "topside equipment list"):
            return name
    # else: the sheet with the largest used range
    return max(wb.sheetnames, key=lambda n: wb[n].max_row * wb[n].max_column)
