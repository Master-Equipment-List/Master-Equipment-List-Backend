"""Try different field names and direct env var setting."""
import os
import sys

sys.path.insert(0, ".")
from pydantic_settings import BaseSettings, SettingsConfigDict

# Test 1: rename to AI_API_KEY
class T1(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    AI_API_KEY: str = "DEFAULT"
    ANTHROPIC_API_KEY: str = "DEFAULT"

t = T1()
print(f"AI_API_KEY: {t.AI_API_KEY[:30]!r}")
print(f"ANTHROPIC_API_KEY: {t.ANTHROPIC_API_KEY[:30]!r}")

# Test 2: set via os.environ before instantiating
print()
os.environ["ANTHROPIC_API_KEY"] = "sk-test-from-environ"
class T2(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    ANTHROPIC_API_KEY: str = "DEFAULT"

t2 = T2()
print(f"After os.environ set, ANTHROPIC_API_KEY: {t2.ANTHROPIC_API_KEY!r}")
del os.environ["ANTHROPIC_API_KEY"]

# Test 3: load with python-dotenv into os.environ first
print()
from dotenv import load_dotenv
load_dotenv(".env")
print(f"After load_dotenv, os.environ has ANTHROPIC_API_KEY? {'YES' if os.environ.get('ANTHROPIC_API_KEY') else 'NO'}")
val = os.environ.get('ANTHROPIC_API_KEY', '')
print(f"  length: {len(val)}, starts: {val[:30]!r}")

class T3(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")
    ANTHROPIC_API_KEY: str = "DEFAULT"

t3 = T3()
print(f"After load_dotenv + new Settings(), ANTHROPIC_API_KEY: len={len(t3.ANTHROPIC_API_KEY)}  starts={t3.ANTHROPIC_API_KEY[:30]!r}")
