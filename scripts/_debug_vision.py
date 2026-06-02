"""Debug vision service — find out exactly why it's not running."""
import sys
import traceback

sys.path.insert(0, ".")

print("=== Step 1: settings ===")
from app.config import settings
print(f"  ANTHROPIC_API_KEY set? {'YES' if settings.ANTHROPIC_API_KEY else 'NO'}")
print(f"  ANTHROPIC_API_KEY prefix: {settings.ANTHROPIC_API_KEY[:25]}...")
print(f"  VISION_MODEL: {settings.VISION_MODEL}")
print(f"  POPPLER_PATH: {settings.POPPLER_PATH!r}")

print()
print("=== Step 2: vision service module ===")
try:
    from app.services import vision_pfd_service
    print(f"  is_enabled() = {vision_pfd_service.is_enabled()}")
except Exception as e:
    print(f"  IMPORT FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=== Step 3: anthropic SDK ===")
try:
    from anthropic import Anthropic
    print(f"  anthropic SDK installed")
except ImportError as e:
    print(f"  anthropic NOT INSTALLED: {e}")
    sys.exit(1)

print()
print("=== Step 4: pdf2image + poppler ===")
try:
    from pdf2image import convert_from_path
    kwargs = {}
    if settings.POPPLER_PATH:
        kwargs["poppler_path"] = settings.POPPLER_PATH
    pdf_path = r"D:\targeticon\Master Equipment List\MEL POC data\Topsides_20171\PFD Samples\20171-SPOG-81700-PR-DW-1031_00_FLARE AND CLOSED DRAIN SYSTEM.pdf"
    images = convert_from_path(pdf_path, dpi=200, last_page=1, **kwargs)
    print(f"  Rendered {len(images)} page(s); first page size: {images[0].size}")
except Exception as e:
    print(f"  RENDER FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=== Step 5: anthropic API call ===")
try:
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    # quick text-only ping
    resp = client.messages.create(
        model=settings.VISION_MODEL,
        max_tokens=20,
        messages=[{"role": "user", "content": "Reply with the word 'OK' only."}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    print(f"  API responded: {text!r}")
except Exception as e:
    print(f"  API CALL FAILED: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=== Step 6: full vision PFD call ===")
try:
    result = vision_pfd_service.extract_pfd_with_vision(pdf_path)
    if result:
        print(f"  SUCCESS — tags_found: {result.get('tags_found')}")
        print(f"  equipment rows: {len(result.get('equipment_table') or [])}")
    else:
        print("  Returned None")
except Exception as e:
    print(f"  EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
