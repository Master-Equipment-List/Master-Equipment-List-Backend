"""Plain-text parser for .txt, .md, .log, .json, .xml etc."""
from pathlib import Path

import chardet

from app.parsers.base import ParseResult


def parse_text(path: str | Path) -> ParseResult:
    res = ParseResult(parser="text")
    p = Path(path)
    try:
        raw = p.read_bytes()
        enc = chardet.detect(raw[:65536]).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")
        res.data = {"text": text, "encoding": enc}
    except Exception as e:
        res.status = "error"
        res.error = str(e)
    return res
