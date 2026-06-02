"""Pretty-print the result of parse_one_file on the PFD."""
import json
import pprint
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"storage\pfd_bbox.json"
d = json.load(open(path, encoding="utf-8-sig"))
pfd = d.get("pfd", {})
print("status:", d.get("status"), "used_ocr:", d.get("used_ocr"))
print("tags_found:", pfd.get("tags_found"))
print()
print("=== equipment_table ===")
for row in pfd.get("equipment_table", []):
    print(f'  col{row["column"]}  {row["tag"]}')
    print(f'      description : {row.get("description")}')
    print(f'      config      : {row.get("configuration")}')
    print(f'      operating   : {row.get("operating")}')
    print(f'      design      : {row.get("design")}')
    print(f'      material    : {row.get("material")}')
print()
print("=== document_metadata ===")
md = pfd.get("document_metadata") or {}
for k, v in md.items():
    if k != "revisions":
        print(f"  {k} = {v}")
print("  revisions:")
for r in md.get("revisions") or []:
    print(f"    {r}")
print()
print("=== reference_drawings ===")
for r in pfd.get("reference_drawings", []):
    print(f"  {r}")
print()
print("=== notes ===")
for n in pfd.get("notes", []):
    print(f"  {n}")
print()
print("=== process_connections ===")
pprint.pprint(pfd.get("process_connections"))
