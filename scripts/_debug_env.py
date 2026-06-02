"""Check whether .env is parseable and what python-dotenv reads."""
import os
import sys

# Show BOM if any
with open(".env", "rb") as f:
    head = f.read(8)
print(f"First 8 bytes: {head!r}")

# python-dotenv direct read
from dotenv import dotenv_values
v = dotenv_values(".env")
print(f"Total keys read by dotenv: {len(v)}")
print(f"ANTHROPIC_API_KEY in dotenv? {'YES' if 'ANTHROPIC_API_KEY' in v else 'NO'}")
ak = v.get("ANTHROPIC_API_KEY") or ""
print(f"  value length: {len(ak)}")
print(f"  starts with: {ak[:30]!r}")
print(f"  ends with: {ak[-20:]!r}" if ak else "")

# pydantic settings
print()
sys.path.insert(0, ".")
from app.config import settings
print(f"Pydantic ANTHROPIC_API_KEY length: {len(settings.ANTHROPIC_API_KEY)}")
print(f"Pydantic ANTHROPIC_API_KEY starts with: {settings.ANTHROPIC_API_KEY[:30]!r}")

# os.environ check
print(f"os.environ ANTHROPIC_API_KEY set? {'YES' if os.environ.get('ANTHROPIC_API_KEY') else 'NO'}")
