"""PFD extractor — format-agnostic vision pass-through.

No heuristics, no regex, no fixed schema. The vision model reads each page
of the PDF and returns whatever JSON it produces; we pass that through
unchanged. The caller stores the raw JSON for display / review and does NOT
attempt to auto-apply field updates (the shape is not guaranteed).
"""
from __future__ import annotations

from typing import Any

from app.services import vision_pfd_service


def _empty_result(source: str = "none") -> dict[str, Any]:
    return {
        "pages": [],
        "pfd_updates": {},
        "tags_found": [],
        "_source": source,
    }


def extract_pfd(
    extraction_data: dict[str, Any] | None = None,
    *,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Read a PFD via vision and return the raw generic JSON.

    Output shape (NOT a fixed schema — pages[*] varies per document):
        {
          "pages": [<raw per-page JSON from Claude>, ...],
          "pfd_updates": {},      # always empty — no auto-apply
          "tags_found": [],       # always empty — no inference
          "_source": "vision" | "vision_disabled" | "vision_failed"
        }
    """
    if not source_path:
        return _empty_result(source="no_source_path")
    if not vision_pfd_service.is_enabled():
        return _empty_result(source="vision_disabled")

    result = vision_pfd_service.extract_pfd_with_vision(source_path)
    if not result:
        return _empty_result(source="vision_failed")

    # Preserve raw vision output. No normalization, no shape coercion.
    result.setdefault("pages", [])
    result["pfd_updates"] = {}
    result["tags_found"] = []
    result["_source"] = "vision"
    return result


def extract_pfd_updates(
    extraction_data: dict[str, Any] | None = None,
    *,
    source_path: str | None = None,
) -> dict[str, dict[str, str]]:
    """Backwards-compat: auto-apply is disabled, so this is always empty."""
    return {}
