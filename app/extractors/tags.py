"""Shared utilities for finding equipment tags in extracted text/tables."""
import re
from typing import Iterable

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


# Character pairs a vision model commonly confuses when transcribing small
# text on engineering drawings (e.g. reading the "S" in a tag as "5").
# Used only as a fallback match against tags that ALREADY exist in the
# project — never to invent a tag from scratch.
_CONFUSABLE = {
    "5": "S", "S": "5",
    "0": "O", "O": "0",
    "1": "I", "I": "1",
    "8": "B", "B": "8",
    "2": "Z", "Z": "2",
    "6": "G", "G": "6",
}


def find_fuzzy_tag_match(tag: str, known_tags: Iterable[str]) -> str | None:
    """Match ``tag`` against ``known_tags`` (already-normalized) allowing
    exactly ONE commonly-confused character to differ.

    Call this only after an exact match has failed. Returns the matching
    known tag, or ``None`` if zero or more than one known tag would match
    — ambiguous cases are left unresolved rather than guessed.
    """
    t = normalize_tag(tag)
    known = set(known_tags)
    if t in known:
        return t
    candidates: set[str] = set()
    for i, ch in enumerate(t):
        repl = _CONFUSABLE.get(ch)
        if repl is None:
            continue
        candidate = t[:i] + repl + t[i + 1:]
        if candidate in known:
            candidates.add(candidate)
    return candidates.pop() if len(candidates) == 1 else None
