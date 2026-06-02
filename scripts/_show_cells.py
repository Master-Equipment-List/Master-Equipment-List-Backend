"""Show the actual concatenated cell text per column."""
import json
import sys

sys.path.insert(0, ".")
from app.extractors.pfd_extractor import (
    _merge_tag_fragments,
    _identify_row_lines,
    _group_by_line,
    _is_tag,
    _clean_tag_text,
    _interpolate_missing_columns,
    _dedupe_tag_columns,
    _build_columns,
    _which_column,
    _smart_join,
    _parse_press_temp_cell,
)

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
words = d["data"]["pages_boxes"][0]
words = _merge_tag_fragments(words)
tag_lines, desc_lines, op_lines, design_lines, mat_lines = _identify_row_lines(words)
print("op_lines:", op_lines)
print("desc_lines:", desc_lines)
print("mat_lines:", mat_lines)

by_line = _group_by_line(words)
tag_words = sorted(
    [w for w in by_line[tag_lines[0]] if _is_tag(_clean_tag_text(w["text"]))],
    key=lambda w: w["left"],
)
for tw in tag_words:
    tw["text"] = _clean_tag_text(tw["text"])
tag_words = _interpolate_missing_columns(tag_words)
tag_words = _dedupe_tag_columns(tag_words)
columns = _build_columns(tag_words)

# Re-implement the band/cell collection here, with prints
op_band = []
for li in op_lines:
    op_band.extend(by_line.get(li, []))
op_band = [w for w in op_band if not _is_tag(_clean_tag_text(w["text"]))]
from collections import defaultdict
col_words = defaultdict(list)
for w in op_band:
    xc = w["left"] + w["width"] / 2
    ci = _which_column(xc, columns)
    if ci is not None:
        col_words[ci].append(w)
for ci, ws in col_words.items():
    ws.sort(key=lambda w: (w["top"], w["left"]))
    joined = _smart_join([w["text"] for w in ws])
    parsed = _parse_press_temp_cell(joined)
    print(f"col{ci+1}  tag={columns[ci]['tag']:<14}  cell={joined!r}  parsed={parsed}")
