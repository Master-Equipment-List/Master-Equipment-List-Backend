"""Inspect raw operating-row cell contents per column to see why temp parsing fails."""
import json

d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
boxes = d["data"]["pages_boxes"][0]

# crude grouping by line near y=604 (the OPERATING row)
op = [w for w in boxes if 580 <= w["top"] <= 660]
op.sort(key=lambda w: w["left"])
print("Operating row words (left -> right):")
for w in op:
    print(f"  x={w['left']:>5}  y={w['top']:>4}  text={w['text']!r}")
