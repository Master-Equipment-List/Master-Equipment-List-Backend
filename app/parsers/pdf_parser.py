"""PDF parser with OCR fallback.

For text-based PDFs we use pdfplumber. For CAD drawings (PFDs / P&IDs)
where text isn't recoverable from the PDF text layer, we rasterize each
page and OCR it with Tesseract.

Two OCR outputs are produced when OCR runs:

* ``pages_text`` — the plain reading-order text from ``image_to_string``.
  Useful for full-text search and the file viewer.
* ``pages_boxes`` — per-word bounding boxes from ``image_to_data``.
  Lets downstream extractors reconstruct columnar tables (equipment
  header bands on PFDs) by clustering words by Y and X.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber

from app.config import settings
from app.parsers.base import ParseResult

MIN_TEXT_LEN_PER_PAGE = 40


def _is_scanned(text_pages: list[str]) -> bool:
    if not text_pages:
        return True
    total_len = sum(len(t or "") for t in text_pages)
    avg = total_len / max(len(text_pages), 1)
    return avg < MIN_TEXT_LEN_PER_PAGE


def _ocr_pages(path: str, dpi: int = 400) -> tuple[list[str], list[list[dict[str, Any]]]]:
    """Run OCR over each page and return ``(pages_text, pages_boxes)``.

    ``pages_boxes`` is a list (one per page) of word dicts with keys
    ``text, left, top, width, height, page, line, word, conf``.
    """
    try:
        from pdf2image import convert_from_path  # lazy
        import pytesseract
    except ImportError as e:
        raise RuntimeError(f"OCR dependencies not installed: {e}")

    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
    convert_kwargs = {}
    if settings.POPPLER_PATH:
        convert_kwargs["poppler_path"] = settings.POPPLER_PATH

    images = convert_from_path(path, dpi=dpi, **convert_kwargs)

    # PSM 6 (uniform block) reads the equipment header table reliably AND
    # provides word-level bounding boxes. PSM 11 (sparse text) finds more
    # scattered diagram labels but loses cohesion in the header band — we
    # prioritise the header.
    config = "--psm 6"

    pages_text: list[str] = []
    pages_boxes: list[list[dict[str, Any]]] = []
    for img in images:
        # Reading-order text + word boxes from the same PSM run.
        pages_text.append(pytesseract.image_to_string(img, config=config))
        data = pytesseract.image_to_data(
            img, config=config, output_type=pytesseract.Output.DICT
        )
        words: list[dict[str, Any]] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = int(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            words.append({
                "text": text,
                "left": int(data["left"][i]),
                "top": int(data["top"][i]),
                "width": int(data["width"][i]),
                "height": int(data["height"][i]),
                "page": int(data.get("page_num", [1] * n)[i]),
                "line": int(data.get("line_num", [0] * n)[i]),
                "word": int(data.get("word_num", [0] * n)[i]),
                "conf": conf,
                "image_width": img.width,
                "image_height": img.height,
            })
        pages_boxes.append(words)

    return pages_text, pages_boxes


def parse_pdf(path: str | Path, force_ocr: bool = False, ocr_dpi: int = 400) -> ParseResult:
    """Parse a PDF.

    Routing — format-agnostic by design:
    1. If the the LLM vision service is configured, use it. It handles
       text-based PDFs, scanned PDFs, and engineering drawings uniformly,
       returning per-page JSON of whatever it sees.
    2. Otherwise fall back to pdfplumber text extraction + Tesseract OCR.

    The vision path always wins when available because it works on every
    PDF type and produces the same shape the file viewer renders.
    """
    path = str(path)
    res = ParseResult(parser="pdf")

    # ---- Path A: vision (preferred) ---------------------------------------
    try:
        from app.services import vision_pfd_service
    except ImportError:
        vision_pfd_service = None  # type: ignore[assignment]

    if vision_pfd_service is not None and vision_pfd_service.is_enabled():
        try:
            v = vision_pfd_service.extract(path)
        except Exception as e:  # noqa: BLE001
            v = None
            res.error = f"vision extraction failed: {e}"
        if v and v.get("pages"):
            res.used_ocr = False
            res.pages = len(v["pages"])
            res.data = {
                # The raw per-page vision JSON — this is what the file
                # viewer renders. Shape varies per document, by design.
                "vision_pages": v["pages"],
                # Convenience aggregate for full-text search: every string
                # value in the per-page JSON, flattened. Optional — UI
                # doesn't need to render it.
                "text": _flatten_strings(v["pages"]),
            }
            return res
        # Vision returned nothing. This is almost always a poppler /
        # pdf2image issue on the deploy target: the exception is caught
        # inside `vision_pfd_service.extract()` (which returns None) and
        # never bubbles up as an error. Without a visible signal here,
        # the file just quietly falls back to pdfplumber — which doesn't
        # produce `vision_pages`, so every field mapper (PFD / P&ID /
        # Vendor) silently skips the file and the sync summary shows
        # "0 updates applied" with no explanation. Surface the failure
        # so ops can see it in the extraction record.
        if res.error is None:
            res.error = (
                "vision pipeline returned no pages — most likely "
                "poppler-utils / pdf2image is missing on the server. "
                "Falling back to pdfplumber text extraction, which will "
                "NOT populate the field mappers. Install poppler-utils "
                "on the deployment host (Render: run build.sh in the "
                "build command)."
            )

    # ---- Path B: pdfplumber + OCR fallback --------------------------------
    pages_text: list[str] = []
    tables: list[list[list[str | None]]] = []
    pages_boxes: list[list[dict[str, Any]]] = []

    if not force_ocr:
        try:
            with pdfplumber.open(path) as pdf:
                res.pages = len(pdf.pages)
                for page in pdf.pages:
                    try:
                        pages_text.append(page.extract_text() or "")
                    except Exception:
                        pages_text.append("")
                    try:
                        for t in page.extract_tables() or []:
                            tables.append(t)
                    except Exception:
                        pass
        except Exception as e:
            res.status = "error"
            res.error = f"pdfplumber failed: {e}"
            return res

    if force_ocr or _is_scanned(pages_text):
        try:
            ocr_text, ocr_boxes = _ocr_pages(path, dpi=ocr_dpi)
            pages_text = ocr_text
            pages_boxes = ocr_boxes
            res.used_ocr = True
            res.pages = len(ocr_text)
        except Exception as e:
            res.error = f"OCR fallback failed: {e}"

    res.data = {
        "pages_text": pages_text,
        "text": "\n\n".join(pages_text),
        "tables": tables,
        "pages_boxes": pages_boxes,
    }
    return res


def _flatten_strings(obj: Any) -> str:
    """Recursively collect every string leaf from a nested dict/list, joined
    with newlines. Used for full-text search across the vision JSON.
    """
    out: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            if x.strip():
                out.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(obj)
    return "\n".join(out)
