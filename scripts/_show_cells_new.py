"""Show cells using the SAME sort as the real extractor (_row_then_x_sort)."""
import json
import sys

sys.path.insert(0, ".")
from collections import defaultdict
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
    _row_then_x_sort,
    _fuzzy_label_match,
    LABELS,
)

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
words = d["data"]["pages_boxes"][0]
words = _merge_tag_fragments(words)
tag_lines, desc_lines, op_lines, design_lines, mat_lines = _identify_row_lines(words)
print("op_lines:", op_lines)

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

first_tag_x = min(
    (w["left"] for w in by_line[tag_lines[0]] if _is_tag(_clean_tag_text(w["text"]))),
    default=None,
)

# Mirror _band_words from the extractor
op_band = []
for li in op_lines:
    op_band.extend(by_line.get(li, []))
filtered = []
for w in op_band:
    if _is_tag(_clean_tag_text(w["text"])):
        continue
    if first_tag_x is not None and w["left"] + w["width"] < first_tag_x:
        continue
    if any(_fuzzy_label_match(w["text"], L) for L in LABELS):
        continue
    filtered.append(w)
op_band = filtered

col_words = defaultdict(list)
for w in op_band:
    xc = w["left"] + w["width"] / 2
    ci = _which_column(xc, columns)
    if ci is not None:
        col_words[ci].append(w)
for ci in sorted(col_words):
    ordered = _row_then_x_sort(col_words[ci])
    cell = _smart_join([w["text"] for w in ordered])
    parsed = _parse_press_temp_cell(cell)
    tag = columns[ci]["tag"]
    print(f"col{ci+1:>2}  tag={tag:<14}  cell={cell!r}  parsed={parsed}")
