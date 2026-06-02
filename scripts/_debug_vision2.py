"""Try vision call directly with detailed errors."""
import sys
import traceback
sys.path.insert(0, ".")

from app.config import settings
print(f"API key set: {bool(settings.ANTHROPIC_API_KEY)}")
print(f"DPI: {settings.VISION_RENDER_DPI}")

from app.services import vision_pfd_service
pdf_path = r"D:\targeticon\Master Equipment List\MEL POC data\Topsides_20171\PFD Samples\20171-SPOG-81700-PR-DW-1031_00_FLARE AND CLOSED DRAIN SYSTEM.pdf"

# Render and tile
from pdf2image import convert_from_path
kwargs = {}
if settings.POPPLER_PATH:
    kwargs["poppler_path"] = settings.POPPLER_PATH
images = convert_from_path(pdf_path, dpi=settings.VISION_RENDER_DPI, last_page=1, **kwargs)
img = images[0]
print(f"Full page rendered: {img.size}")

tiles = vision_pfd_service._tile_pfd_page(img)
print(f"Number of tiles: {len(tiles)}")
import base64
for i, b in enumerate(tiles):
    decoded = base64.b64decode(b)
    print(f"  tile {i}: {len(decoded)} bytes ({len(decoded)/1024/1024:.2f} MB)")

# Try the actual call
print()
print("=== Calling Claude ===")
try:
    result = vision_pfd_service._call_claude(tiles, vision_pfd_service.PFD_SYSTEM_PROMPT, vision_pfd_service.PFD_USER_PROMPT)
    if result is None:
        print("Call returned None (check earlier log warnings)")
    else:
        print(f"SUCCESS: keys = {list(result.keys())}")
        et = result.get("equipment_table") or []
        print(f"equipment_table count: {len(et)}")
        if et:
            print(f"first row: {et[0]}")
except Exception as e:
    traceback.print_exc()
