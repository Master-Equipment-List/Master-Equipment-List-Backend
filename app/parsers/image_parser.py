"""Image parser — OCR the image with Tesseract."""
from pathlib import Path

from app.config import settings
from app.parsers.base import ParseResult


def parse_image(path: str | Path) -> ParseResult:
    res = ParseResult(parser="image", used_ocr=True)
    try:
        from PIL import Image
        import pytesseract
    except ImportError as e:
        res.status = "error"
        res.error = f"OCR dependencies missing: {e}"
        return res

    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

    try:
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img)
            res.data = {
                "text": text,
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
            }
    except Exception as e:
        res.status = "error"
        res.error = f"image OCR failed: {e}"
    return res
