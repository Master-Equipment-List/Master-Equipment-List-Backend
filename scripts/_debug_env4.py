"""Trace exactly what happens during config import."""
import os
import sys
sys.path.insert(0, ".")

print(f"BEFORE import: os.environ ANTHROPIC_API_KEY: {os.environ.get('ANTHROPIC_API_KEY', '<not set>')[:30]!r}")

from app.config import settings

print(f"AFTER import:  os.environ ANTHROPIC_API_KEY: {os.environ.get('ANTHROPIC_API_KEY', '<not set>')[:30]!r}")
print(f"AFTER import:  settings.ANTHROPIC_API_KEY:  len={len(settings.ANTHROPIC_API_KEY)} starts={settings.ANTHROPIC_API_KEY[:30]!r}")
