import json

d = json.load(open(r"storage\pfd_vision.json", encoding="utf-8-sig"))
pfd = d.get("pfd", {})
print("_source:", pfd.get("_source"))
print("tags_found:", pfd.get("tags_found"))
print()
print("=== equipment_table ===")
for row in pfd.get("equipment_table") or []:
    print(f'  col{row["column"]:>2}  {row["tag"]:<14}')
    print(f'      description : {row.get("description")}')
    print(f'      config      : {row.get("configuration")}')
    print(f'      operating   : {row.get("operating")}')
    print(f'      design      : {row.get("design")}')
    print(f'      material    : {row.get("material")}')
print()
md = pfd.get("document_metadata") or {}
print("=== document_metadata ===")
for k in ("title", "contractor_doc_id", "contractor_job_no", "project_name",
          "company", "contractor", "consultant", "facility_name", "location",
          "scale", "sheet", "paper_size"):
    print(f"  {k}: {md.get(k)!r}")
print(f"  revisions: {len(md.get('revisions') or [])}")
for r in (md.get("revisions") or [])[:4]:
    print(f"    {r}")
print()
print("=== reference_drawings ===")
for s in pfd.get("reference_drawings") or []:
    print(f"  {s}")
print()
print("=== notes ===")
for s in pfd.get("notes") or []:
    print(f"  {s}")
print()
print("=== process_connections ===")
import pprint
pprint.pprint(pfd.get("process_connections"))
