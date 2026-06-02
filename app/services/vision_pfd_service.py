"""Format-agnostic vision-based PDF extraction via Claude.

Design principle (per user mandate): this module contains NO domain schema,
NO fixed crop regions, NO assumed row/column labels, NO regex patterns for
specific fields, and NO MEL-shaped output. Every page is rendered as a single
image and passed to Claude with one generic prompt asking the model to return
whatever it sees as JSON. The shape of the output adapts to whatever the
document contains — different drawings produce different shapes, by design.

Two public entry points are preserved for backwards compatibility with the
existing extractor/sync wiring, but both now return the same raw generic
JSON: a dict with a single ``pages`` key whose value is a list of per-page
JSON objects exactly as Claude returned them.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic prompts — no domain assumptions
# ---------------------------------------------------------------------------

GENERIC_SYSTEM_PROMPT = """You are a careful document analyst. You read a \
page of a PDF and return what is visually present as JSON. Do not invent \
content, do not normalize values, do not infer meaning. Report only what is \
literally printed on the page, preserving original punctuation, spacing, \
and casing as far as the JSON syntax allows. Return ONLY a JSON object — \
no markdown fences, no commentary."""


GENERIC_USER_PROMPT = """Read this image and return its content as JSON.

This image is ONE view of a page from a document: either the FULL PAGE \
(for layout context) or a single TILE (one geometric slice of the page, \
showing a portion of its content at higher resolution).

The document may be ANY kind — an engineering drawing, a vendor data \
sheet, a form, a spreadsheet, a memo, a scanned image. There is no fixed \
schema. Choose top-level keys that naturally describe what you actually \
see in THIS image. Suggested (NOT required) starting keys, when applicable:

  - "title_block":         the drawing/document title block
  - "revisions":            revision history rows
  - "tables":               list of table objects, each {"name": str|null, \
"headers": [str], "rows": [[str]]}
  - "notes":                numbered or bulleted notes
  - "reference_drawings":   referenced document IDs
  - "labels":               diagram labels / callouts / annotations
  - "dimensions":           dimensions / measurements visible
  - "other_text":           any other readable text not covered above

Adapt freely. If this image has nothing resembling a table, omit "tables". \
If there is no title block, omit "title_block". If you see something that \
doesn't fit any of these, add your own key for it. The goal is faithful \
representation of THIS image, not conformance to a template.

Rules:
1. Report literal printed content only. Never invent, never normalize units.
2. If a value is unreadable, use null rather than guessing.
3. Preserve original punctuation, casing, and spacing inside string values.
4. Use empty arrays / null for absent sections rather than fabricating data.
5. Return ONLY the JSON object — no markdown fences, no prose, no \
explanations."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """True when an API key is configured (vision is opt-in via .env)."""
    return bool(settings.ANTHROPIC_API_KEY)


def _render_pages(pdf_path: str | Path, max_pages: int, dpi: int) -> list:
    """Render up to ``max_pages`` of a PDF to PIL Images."""
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        raise RuntimeError(f"pdf2image not installed: {e}")
    kwargs: dict[str, Any] = {}
    if settings.POPPLER_PATH:
        kwargs["poppler_path"] = settings.POPPLER_PATH
    if max_pages and max_pages > 0:
        kwargs["last_page"] = max_pages
    return convert_from_path(str(pdf_path), dpi=dpi, **kwargs)


def _image_to_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict[str, Any] | None:
    text = _strip_json_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _tile_image(img, cols: int, rows: int, overlap_frac: float = 0.04) -> list:
    """Slice a PIL image into a regular ``cols`` x ``rows`` grid with small
    overlap between adjacent tiles. Purely geometric — no knowledge of what
    is in any tile. Returns the tile images in reading order (left-to-right,
    top-to-bottom).
    """
    w, h = img.size
    tile_w = w / cols
    tile_h = h / rows
    pad_x = int(tile_w * overlap_frac)
    pad_y = int(tile_h * overlap_frac)
    tiles = []
    for r in range(rows):
        for c in range(cols):
            x0 = max(0, int(c * tile_w) - pad_x)
            y0 = max(0, int(r * tile_h) - pad_y)
            x1 = min(w, int((c + 1) * tile_w) + pad_x)
            y1 = min(h, int((r + 1) * tile_h) + pad_y)
            tiles.append(img.crop((x0, y0, x1, y1)))
    return tiles


def _call_claude_on_image(img, label: str) -> dict[str, Any]:
    """Send a SINGLE image to Claude with the generic prompt. Each call has
    a small, focused output that fits comfortably in max_tokens — so dense
    pages (vendor data sheets, equipment headers) don't get truncated.

    Always returns a dict — never None — so the caller can record failures
    in the page output instead of silently losing content.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return {"_label": label, "_extraction_error": "anthropic_sdk_missing"}

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    image_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": _image_to_b64(img),
        },
    }
    try:
        resp = client.messages.create(
            model=settings.VISION_MODEL,
            max_tokens=8192,
            temperature=0,
            system=[{
                "type": "text",
                "text": GENERIC_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": GENERIC_USER_PROMPT},
                ],
            }],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Anthropic vision call failed for %s: %s", label, e)
        return {"_label": label, "_extraction_error": f"api_call_failed: {e}"}

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    parsed = _parse_json(text)
    if parsed is None:
        return {
            "_label": label,
            "_extraction_error": "json_parse_failed",
            "_stop_reason": getattr(resp, "stop_reason", None),
            "_raw_text_preview": text[:4000],
        }
    parsed["_label"] = label
    return parsed


def _call_claude_on_page(img) -> dict[str, Any]:
    """Robust per-page extraction. We make ONE Claude call per image:
       - The full-page overview (for layout context).
       - Each tile of the geometric grid (for small-text legibility).

    Every image becomes a separate API call with its own JSON output. This
    guarantees: (a) no single response can truncate and lose the whole page,
    and (b) a failure on any one tile is recorded but doesn't drop the rest.
    """
    cols = max(1, settings.VISION_TILE_COLS)
    rows = max(1, settings.VISION_TILE_ROWS)

    page_result: dict[str, Any] = {}

    # Overview — sees the whole page, picks up large items (title block,
    # overall layout, big labels) without small-text precision.
    page_result["overview"] = _call_claude_on_image(img, label="overview")

    # Tiles — each is a high-resolution slice. The grid is mechanical, no
    # assumption about what's in each cell.
    if cols * rows > 1:
        tiles = _tile_image(img, cols, rows)
        tile_results: list[dict[str, Any]] = []
        for idx, tile in enumerate(tiles):
            r, c = divmod(idx, cols)
            label = f"tile_r{r}_c{c}"
            tile_results.append(_call_claude_on_image(tile, label=label))
        page_result["tiles"] = tile_results

    return page_result


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def extract(pdf_path: str | Path) -> dict[str, Any] | None:
    """Format-agnostic PDF extraction. Returns:

        {"pages": [<per-page JSON from Claude>, ...]}

    Each page's JSON is whatever Claude produced from a single generic
    prompt. The shape is NOT enforced or normalized in any way.
    """
    if not is_enabled():
        return None
    try:
        pages = _render_pages(
            pdf_path, settings.VISION_MAX_PAGES, settings.VISION_RENDER_DPI
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Vision: failed to render PDF %s: %s", pdf_path, e)
        return None
    if not pages:
        return None

    page_results: list[dict[str, Any]] = []
    for page_idx, img in enumerate(pages, start=1):
        result = _call_claude_on_page(img)
        # Tag every page with its 1-based index so downstream consumers
        # know which page they're looking at.
        result["_page_index"] = page_idx
        page_results.append(result)

    return {"pages": page_results} if page_results else None


# Backwards-compat aliases — both go through the same generic extractor.
def extract_pfd_with_vision(pdf_path: str | Path) -> dict[str, Any] | None:
    return extract(pdf_path)


def extract_vendor_fields_with_vision(
    pdf_path: str | Path,
) -> dict[str, Any] | None:
    return extract(pdf_path)
