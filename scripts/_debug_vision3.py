"""Direct call to Anthropic API with tiled images, raise on error."""
import sys, base64
sys.path.insert(0, ".")
from app.config import settings
from app.services import vision_pfd_service

pdf_path = r"D:\targeticon\Master Equipment List\MEL POC data\Topsides_20171\PFD Samples\20171-SPOG-81700-PR-DW-1031_00_FLARE AND CLOSED DRAIN SYSTEM.pdf"
from pdf2image import convert_from_path
kwargs = {}
if settings.POPPLER_PATH:
    kwargs["poppler_path"] = settings.POPPLER_PATH
images = convert_from_path(pdf_path, dpi=settings.VISION_RENDER_DPI, last_page=1, **kwargs)
img = images[0]
tiles = vision_pfd_service._tile_pfd_page(img)

from anthropic import Anthropic
client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
content = []
for b64 in tiles:
    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
content.append({"type": "text", "text": vision_pfd_service.PFD_USER_PROMPT})

print("Calling API with", len(tiles), "images...")
resp = client.messages.create(
    model=settings.VISION_MODEL,
    max_tokens=4096,
    temperature=0,
    system=vision_pfd_service.PFD_SYSTEM_PROMPT,
    messages=[{"role": "user", "content": content}],
)
print("Stop reason:", resp.stop_reason)
print("Usage:", resp.usage)
print()
for blk in resp.content:
    if hasattr(blk, 'text'):
        print("=== text block ===")
        print(blk.text[:3000])
