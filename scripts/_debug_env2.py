"""Find which line ANTHROPIC_API_KEY is on and inspect its raw bytes."""
with open(".env", "rb") as f:
    raw = f.read()

# Show all lines containing ANTHROPIC
for i, line in enumerate(raw.split(b"\n")):
    if b"ANTHROPIC" in line.upper() or b"VISION" in line.upper():
        print(f"line {i}: len={len(line)}  bytes[:80]={line[:80]!r}")

# Test what pydantic-settings does directly
import sys
sys.path.insert(0, ".")
from pydantic_settings import BaseSettings, SettingsConfigDict

class TestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    ANTHROPIC_API_KEY: str = "DEFAULT"
    VISION_MODEL: str = "DEFAULT"

t = TestSettings()
print(f"TestSettings ANTHROPIC_API_KEY: len={len(t.ANTHROPIC_API_KEY)} starts={t.ANTHROPIC_API_KEY[:30]!r}")
print(f"TestSettings VISION_MODEL: {t.VISION_MODEL!r}")
