"""Show all drawing-id-shaped tokens in the bbox data and their Y positions."""
import json
import re
from collections import defaultdict

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
boxes = d["data"]["pages_boxes"][0]
page_h = max((w["top"] + w["height"] for w in boxes), default=0)
print(f"page_h = {page_h}")

# group by line
by_line = defaultdict(list)
for w in boxes:
    by_line[w["line"]].append(w)

pat = re.compile(r"\b(\d{4,6})-([A-Z0-9]{2,5})-(\d{4,6})-([A-Z]{2})-([A-Z]{2})-(\d{3,5})\b")
for li, ws in sorted(by_line.items(), key=lambda kv: min(w["top"] for w in kv[1])):
    ws.sort(key=lambda w: w["left"])
    text = " ".join(w["text"] for w in ws).replace("—", "-").replace("–", "-")
    for m in pat.finditer(text):
        y = min(w["top"] for w in ws)
        print(f"  line={li:>3}  y={y:>5}  id={m.group(0)}")
