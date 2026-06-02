"""Dispatch to the right parser based on file extension."""
from pathlib import Path

from app.parsers.base import ParseResult

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_EXCEL_EXTS = {".xlsx", ".xlsm", ".xlsb", ".xls"}
_TEXT_EXTS = {".txt", ".md", ".log", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg"}


def parse_file(path: str | Path, force_ocr: bool = False) -> ParseResult:
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".pdf":
        from app.parsers.pdf_parser import parse_pdf
        return parse_pdf(p, force_ocr=force_ocr)
    if ext in _EXCEL_EXTS:
        from app.parsers.excel_parser import parse_excel
        return parse_excel(p)
    if ext in {".csv", ".tsv"}:
        from app.parsers.csv_parser import parse_csv
        return parse_csv(p)
    if ext in _IMAGE_EXTS:
        from app.parsers.image_parser import parse_image
        return parse_image(p)
    if ext in {".docx", ".docm"}:
        from app.parsers.docx_parser import parse_docx
        return parse_docx(p)
    if ext in _TEXT_EXTS:
        from app.parsers.text_parser import parse_text
        return parse_text(p)

    res = ParseResult(parser="unsupported", status="skipped")
    res.error = f"Unsupported extension: {ext}"
    res.data = {"size_bytes": p.stat().st_size if p.exists() else 0}
    return res
