"""CSV / TSV parser. Encoding is auto-detected with chardet."""
import csv
from pathlib import Path

import chardet

from app.parsers.base import ParseResult


def parse_csv(path: str | Path) -> ParseResult:
    res = ParseResult(parser="csv")
    p = Path(path)
    try:
        raw = p.read_bytes()
        enc = chardet.detect(raw[:65536]).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")
    except Exception as e:
        res.status = "error"
        res.error = f"read failed: {e}"
        return res

    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
    except Exception:
        class _D(csv.excel):
            delimiter = "\t" if p.suffix.lower() == ".tsv" else ","
        dialect = _D

    rows: list[list[str]] = []
    reader = csv.reader(text.splitlines(), dialect)
    for row in reader:
        rows.append(row)

    res.data = {
        "encoding": enc,
        "delimiter": getattr(dialect, "delimiter", ","),
        "rows": rows,
        "row_count": len(rows),
    }
    return res
