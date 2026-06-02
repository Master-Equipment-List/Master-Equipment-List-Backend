"""Print word boxes from the last parse, grouped by Y band."""
import json
from collections import defaultdict

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
boxes = d["data"]["pages_boxes"][0]
print("total words:", len(boxes))
print()

# group by line_num
by_line = defaultdict(list)
for w in boxes:
    by_line[w["line"]].append(w)

# print top 40 lines (the header band)
sorted_lines = sorted(by_line.items(), key=lambda kv: (min(w["top"] for w in kv[1]), kv[0]))
for ln, ws in sorted_lines[:60]:
    ws_sorted = sorted(ws, key=lambda w: w["left"])
    y = min(w["top"] for w in ws)
    print(f"line {ln} (y={y}): " + " | ".join(f'{w["text"]}@{w["left"]}' for w in ws_sorted))
