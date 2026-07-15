"""Vendor data extractor.

Two-stage pipeline:
  1. Vision pass (``vision_pfd_service``) — format-agnostic JSON of every page.
  2. LLM mapper (``vendor_field_mapper``) — pulls just the eight target MEL
     fields + the equipment tag out of that raw JSON.

The first stage stays free of any project-specific schema. The second stage
is where the MEL business requirement (the eight specific columns) lives.
No regex, no fixed positions — the LLM does the semantic mapping.
"""
from __future__ import annotations

from typing import Any

from app.services import vendor_field_mapper, vision_pfd_service


def extract_vendor_data(source_path: str | None) -> dict[str, Any] | None:
    """Return the raw vision JSON + the mapped MEL fields, or ``None``.

    Output shape:
        {
          "pages": [...],                   # raw vision JSON, for review
          "client_equipment_tag": str|None, # what the LLM identified
          "fields": {                       # eight target MEL columns
            "absorbed_power_kw":     str|None,
            "rated_power_kw":        str|None,
            "length_m":              str|None,
            "width_id_m":            str|None,
            "height_tt_m":           str|None,
            "dry_weight_mt":         str|None,
            "operating_weight_mt":   str|None,
            "hydrotest_weight_mt":   str|None,
          },
          "_source": "vision",
        }
    """
    if not source_path or not vision_pfd_service.is_enabled():
        return None
    v = vision_pfd_service.extract_vendor_fields_with_vision(source_path)
    if not v:
        return None

    pages = v.get("pages") or []

    mapping = vendor_field_mapper.map_vendor_fields(pages) if pages else None
    tag = (mapping or {}).get("client_equipment_tag")
    fields_raw = (mapping or {}).get("fields") or {}
    # Drop None / empty values from the apply payload — only fields the
    # mapper actually found are forwarded for update.
    fields = {k: v for k, v in fields_raw.items() if v}

    return {
        "pages": pages,
        "client_equipment_tag": tag,
        "vendor": None,
        "vendor_doc_id": None,
        "equipment_name": None,
        "fields": fields,
        "_source": "vision",
    }


def extract_vendor_fields(
    text: str | None = None,
    *,
    source_path: str | None = None,
) -> dict[str, str]:
    """Backwards-compat: returns only the field-update dict."""
    data = extract_vendor_data(source_path)
    return data["fields"] if data else {}


def extract_vendor_client_tag(source_path: str | None) -> str | None:
    """Backwards-compat: returns just the identified equipment tag."""
    data = extract_vendor_data(source_path)
    return data["client_equipment_tag"] if data else None
