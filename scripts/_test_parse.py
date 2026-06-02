"""Test press/temp parsing on actual cell strings."""
from app.extractors.pfd_extractor import _parse_press_temp_cell, _PRESS_TEMP_PATTERNS

tests = [
    "3.5 barg @ 60°C",
    "3.5 barg @ 60�C",  # OCR variant
    "0.1/3.5 barg @ (-)30/100°C",
    "5.5 barg � 40/100°C",
    "5.5 barg © 40/100°C",
]

for t in tests:
    print(f"Input: {t!r}")
    for i, pat in enumerate(_PRESS_TEMP_PATTERNS):
        m = pat.search(t)
        if m:
            print(f"  pattern{i}: groups={m.groups()}")
    print(f"  _parse_press_temp_cell: {_parse_press_temp_cell(t)}")
    print()
