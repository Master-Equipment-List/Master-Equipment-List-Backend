"""Run the dispatcher + extractors against a single local file and dump JSON.

Usage:
    python -m scripts.parse_one_file "<absolute path>"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.extractors import pfd_extractor, vendor_extractor
from app.extractors.tags import TAG_RE, normalize_tag
from app.parsers import parse_file
from app.services.sync_service import PFD_CATEGORY, VENDOR_CATEGORY, _category_for_path


def main(path: Path) -> None:
    if not path.exists():
        print(json.dumps({"error": f"file not found: {path}"}, indent=2))
        sys.exit(1)

    # Heuristically detect category from path so we can decide whether to force OCR.
    p_str = str(path).replace("\\", "/")
    category = _category_for_path(None, p_str)
    force_ocr = (
        category == PFD_CATEGORY
        and path.suffix.lower() == ".pdf"
    )

    parsed = parse_file(path, force_ocr=force_ocr)
    result = {
        "file": str(path),
        "category": category,
        "parser": parsed.parser,
        "status": parsed.status,
        "error": parsed.error,
        "pages": parsed.pages,
        "used_ocr": parsed.used_ocr,
        "data": parsed.data,
    }

    # Tag scan over extracted text
    text = (parsed.data or {}).get("text", "") if isinstance(parsed.data, dict) else ""
    tags_found = sorted({normalize_tag(t) for t in TAG_RE.findall(text)})
    result["tags_found"] = tags_found

    # If it's a PFD, also run the full PFD extractor (vision first if configured)
    if category == PFD_CATEGORY:
        result["pfd"] = pfd_extractor.extract_pfd(parsed.data or {}, source_path=str(path))

    # If it's vendor data, run the vendor field extractor (vision first if configured)
    if category == VENDOR_CATEGORY:
        result["vendor_fields"] = vendor_extractor.extract_vendor_fields(text, source_path=str(path))

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.parse_one_file <absolute path>")
        sys.exit(2)
    main(Path(sys.argv[1]))
