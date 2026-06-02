"""Shared utilities for finding equipment tags in extracted text/tables."""
import re

# Equipment tags look like V-S68105, P-S68115A/B, H-S68110, A-S75110/120, etc.
# Always: <single equipment letter> - <system letter> <4-6 digits> [optional suffix].
# Allowed suffixes:
#   * A/B  A/B/C   (parallel units, letter only)
#   * /120 /150    (range, digits only)
# This deliberately rejects:
#   - drawing numbers like "DW-1031" (no inner letter)
#   - OCR garble like "P-S671154/8" (mixed digits-and-letters in suffix)
TAG_RE = re.compile(
    r"\b([A-Z]-[A-Z]\d{4,5}(?:[A-Z](?:/[A-Z]){1,2}|/\d{2,4})?)\b"
)


def find_tags_in_text(text: str) -> list[str]:
    if not text:
        return []
    found = TAG_RE.findall(text)
    # Normalize: collapse internal whitespace
    return [re.sub(r"\s+", "", t) for t in found]


def normalize_tag(tag: str) -> str:
    """Compare-friendly form: uppercase, no spaces, em/en-dashes → hyphen."""
    t = (tag or "")
    t = t.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", "", t).upper()


def tag_in_text(tag: str, text: str) -> bool:
    if not tag or not text:
        return False
    t = normalize_tag(tag)
    haystack = re.sub(r"\s+", "", text).upper()
    return t in haystack
