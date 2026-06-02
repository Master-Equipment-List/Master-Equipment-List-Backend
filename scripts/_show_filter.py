import json, sys
sys.path.insert(0, ".")
from app.extractors.pfd_extractor import (
    _merge_tag_fragments, _identify_row_lines, _group_by_line,
    _is_tag, _clean_tag_text, _fuzzy_label_match, LABELS,
)

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
words = d["data"]["pages_boxes"][0]
words = _merge_tag_fragments(words)
tag_lines, _, op_lines, _, _ = _identify_row_lines(words)
by_line = _group_by_line(words)

first_tag_x = min(
    (w["left"] for w in by_line[tag_lines[0]] if _is_tag(_clean_tag_text(w["text"]))),
    default=None,
)
print(f"first_tag_x={first_tag_x}")
print(f"op_lines={op_lines}")
print()

for li in op_lines:
    print(f"=== line {li} ===")
    for w in sorted(by_line[li], key=lambda w: w["left"]):
        is_tag = _is_tag(_clean_tag_text(w["text"]))
        right_of_left = w["left"] + w["width"] < first_tag_x if first_tag_x else False
        is_label = any(_fuzzy_label_match(w["text"], L) for L in LABELS)
        verdict = "KEEP"
        if is_tag: verdict = "DROP(tag)"
        elif right_of_left: verdict = "DROP(left)"
        elif is_label:
            # show which label
            matched = [L for L in LABELS if _fuzzy_label_match(w["text"], L)]
            verdict = f"DROP(label={matched})"
        print(f"  x={w['left']:>5} y={w['top']:>4} text={w['text']!r:<24} -> {verdict}")
