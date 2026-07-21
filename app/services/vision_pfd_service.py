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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# Re-export the shared singleton so callers can still call _get_client().
from app.services._shared_client import get_anthropic_client as _get_client  # noqa: E402


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
    """Render up to ``max_pages`` of a PDF to PIL Images.

    Uses pypdfium2 rather than pdf2image/poppler: pypdfium2 ships a
    prebuilt PDFium binary inside its own pip wheel, so rendering works
    identically in dev and on any deploy target with no system package
    (no apt-get poppler-utils, no build.sh step, no PATH configuration).
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        n_pages = len(doc)
        last = n_pages if not max_pages or max_pages <= 0 else min(max_pages, n_pages)
        scale = dpi / 72.0
        images = []
        for i in range(last):
            page = doc[i]
            try:
                images.append(page.render(scale=scale).to_pil())
            finally:
                page.close()
        return images
    finally:
        doc.close()


# Anthropic's vision API rejects images with any dimension > 8000 px.
# A1 landscape engineering drawings at 400 DPI go to ~9300 × 6600 — well
# over that limit on the long edge. We pre-resize to fit so the call
# doesn't 400. The model resizes its input internally anyway (long edge
# clamped to ~1568 px), so capping at MAX_IMAGE_DIM doesn't lose
# information that wasn't going to be used.
MAX_IMAGE_DIM = 7800  # leave a small safety margin under 8000


def _image_to_b64(img) -> str:
    # Defensive downscale: if either dimension exceeds the API cap,
    # shrink proportionally before encoding.
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIM:
        scale = MAX_IMAGE_DIM / float(max(w, h))
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        try:
            from PIL import Image  # local import — Image already loaded via pdf2image
            img = img.resize(new_size, Image.LANCZOS)
        except Exception:
            img = img.resize(new_size)
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
    """Send a SINGLE image to Claude with the generic prompt.

    Always returns a dict — never None — so the caller can record failures
    in the page output instead of silently losing content.
    """
    try:
        client = _get_client()
    except RuntimeError:
        return {"_label": label, "_extraction_error": "anthropic_sdk_missing"}

    image_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": _image_to_b64(img),
        },
    }
    try:
        # Dense pages — an "AS BUILT" conversion P&ID packing 6-8 full
        # equipment spec rows into one table, say — can need well over
        # 16,000 tokens of JSON output; cut off mid-object it fails to
        # parse and silently drops every tag on that page. Billing is by
        # tokens actually generated, so a higher ceiling costs nothing on
        # pages that already finish comfortably under it. The Anthropic
        # SDK requires streaming once max_tokens implies a long-running
        # response (non-streaming raises above ~16-20k here), so this uses
        # the streaming API to go higher without hitting that guard.
        with client.messages.stream(
            model=settings.VISION_MODEL,
            max_tokens=32000,
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
        ) as stream:
            resp = stream.get_final_message()
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
    """Parallel per-page extraction — overview + all tiles fire concurrently.

    Each image is a separate Claude call with its own JSON output so a
    failure on one tile is recorded without dropping the rest. Running them
    in a ThreadPoolExecutor turns what was N serial API round-trips into a
    single wall-clock wait equal to the slowest call.
    """
    cols = max(1, settings.VISION_TILE_COLS)
    rows = max(1, settings.VISION_TILE_ROWS)

    # Build the work list: (label, image) pairs.
    tasks: list[tuple[str, Any]] = [("overview", img)]
    if cols * rows > 1:
        for idx, tile in enumerate(_tile_image(img, cols, rows)):
            r, c = divmod(idx, cols)
            tasks.append((f"tile_r{r}_c{c}", tile))

    # Fire all calls concurrently — Anthropic SDK is synchronous so we use
    # threads. Max workers = number of tasks so they all start immediately.
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_to_label = {
            pool.submit(_call_claude_on_image, tile_img, label): label
            for label, tile_img in tasks
        }
        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                results[label] = future.result()
            except Exception as e:  # noqa: BLE001
                results[label] = {"_label": label, "_extraction_error": str(e)}

    page_result: dict[str, Any] = {"overview": results["overview"]}
    if cols * rows > 1:
        page_result["tiles"] = [
            results[f"tile_r{r}_c{c}"]
            for r in range(rows)
            for c in range(cols)
        ]
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

    # Process pages concurrently — each page's tiles are already parallelised
    # inside _call_claude_on_page; this outer pool lets multiple pages run at
    # the same time. Cap at 2: each page already fans out to 7 tile calls, so
    # 2 pages × 7 tiles = 14 concurrent API calls — safely within rate limits
    # even when several files are synced in parallel.
    page_results: list[dict[str, Any]] = [{}] * len(pages)
    max_page_workers = min(len(pages), 2)
    with ThreadPoolExecutor(max_workers=max_page_workers) as pool:
        future_to_idx = {
            pool.submit(_call_claude_on_page, img): page_idx
            for page_idx, img in enumerate(pages, start=1)
        }
        for future in as_completed(future_to_idx):
            page_idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001
                result = {"_extraction_error": str(e)}
            result["_page_index"] = page_idx
            page_results[page_idx - 1] = result

    return {"pages": page_results} if page_results else None


# Backwards-compat aliases — both go through the same generic extractor.
def extract_pfd_with_vision(pdf_path: str | Path) -> dict[str, Any] | None:
    return extract(pdf_path)


def extract_vendor_fields_with_vision(
    pdf_path: str | Path,
) -> dict[str, Any] | None:
    return extract(pdf_path)
