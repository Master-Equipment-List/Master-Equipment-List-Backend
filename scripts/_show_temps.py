import json
d = json.load(open(r"storage\pfd_bbox.json", encoding="utf-8-sig"))
et = d["pfd"]["equipment_table"]
print("Operating row per column:")
for row in et:
    op = row.get("operating") or {}
    print(f'  col{row["column"]:>2}  {row["tag"]:<14}  pressure={op.get("pressure_barg")!r:<12}  temp={op.get("temperature_c")!r}')
