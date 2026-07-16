"""Fuzzy duplicate detection for equipment rows.

Two entry points, both built on the same description+type similarity
rules (both must clear their thresholds — description alone matching
isn't enough, too many false positives across genuinely different
equipment that happens to share wording):

  * ``find_duplicate_candidate`` — called by sync_service.py at the
    moment a sync reports a tag that doesn't match any existing row. If
    it's usually genuinely new, but sometimes it's the SAME physical
    equipment under a different/corrected tag (a vision misread, a tag
    renumbering) — this checks whether an EXISTING row's description +
    type are similar enough to flag for admin review instead of blindly
    auto-creating (see ``EquipmentPendingChange.kind ==
    "possible_duplicate"``).

  * ``find_all_duplicate_pairs`` — an on-demand AUDIT across every
    row ALREADY in a project/workspace, independent of syncing. Two rows
    can drift into looking like duplicates of each other without any new
    tag ever being involved (e.g. both edited by hand, or synced from
    different sources at different times) — this is for finding those
    after the fact, not just at the moment a new tag appears.

Both are deliberately conservative: meant to catch likely duplicates, not
flag every vaguely-similar tag.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, Sequence

from app.models import Equipment
from app.services.equipment_create_helper import infer_equipment_type
from app.services.quantity import is_blank

# Deliberately conservative — both must clear these to flag a candidate.
DESCRIPTION_THRESHOLD = 0.72
TYPE_THRESHOLD = 0.75

# Character-level similarity alone is fooled by a short but MEANING-
# REVERSING qualifier swapped into an otherwise-identical description —
# e.g. "LP Flare Knock Out Drum" vs "HP Flare Knock Out Drum" scores 0.96
# (only 2 of ~24 characters differ) despite being two genuinely different
# vessels (different pressure stage). Each group below is a set of mutually
# exclusive qualifiers common in topside/marine equipment descriptions; if
# one description contains a word from a group and the other contains a
# DIFFERENT word from the same group, they're treated as definitely not a
# match regardless of the character ratio. Checked as whole words (via
# regex word-splitting), not substrings, so "LP" doesn't false-match inside
# "help". This is a targeted fix for a demonstrated failure mode, not an
# exhaustive list — extend it if another false positive shows up.
_CONTRASTING_QUALIFIERS = [
    {"lp", "mp", "hp"},              # low / medium / high pressure
    {"inlet", "outlet"},
    {"upstream", "downstream"},
    {"primary", "secondary"},
    {"north", "south"},
    {"east", "west"},
    {"forward", "aft"},
    {"port", "starboard"},
]


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _contrasting_words(wa: set[str], wb: set[str]) -> bool:
    for group in _CONTRASTING_QUALIFIERS:
        hit_a, hit_b = wa & group, wb & group
        if hit_a and hit_b and hit_a != hit_b:
            return True
    return False


def _has_contrasting_qualifier(a: str, b: str) -> bool:
    return _contrasting_words(_words(a), _words(b))


def _normalize(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _similarity(a: str | None, b: str | None) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if _has_contrasting_qualifier(na, nb):
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _type_similarity(a: str | None, b: str | None) -> float:
    """Looser than ``_similarity`` — used only for ``equipment_type``.

    A type string is often a short generic word (especially when inferred
    from a tag prefix, e.g. "Vessel") compared against a more specific
    stored value (e.g. "Pressure Vessel"). Plain character-ratio
    similarity unfairly penalizes this purely for length — SequenceMatcher
    normalizes by combined length, so "Vessel" vs "Pressure Vessel" scores
    ~0.57 despite one obviously being a case of the other. If every word of
    the shorter string appears in the longer one, treat it as a strong
    match; otherwise fall back to the same character-ratio measure
    descriptions use. NOT used for descriptions — there, a missing/extra
    word usually signals genuinely different equipment (e.g. "Cargo Oil
    Pump" vs "Cargo Oil Pump Strainer" must NOT be conflated), so the
    looser subset rule would be actively wrong there.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if _has_contrasting_qualifier(na, nb):
        return 0.0
    wa, wb = _words(na), _words(nb)
    if wa and wb and (wa <= wb or wb <= wa):
        return 0.95
    return difflib.SequenceMatcher(None, na, nb).ratio()


def find_duplicate_candidate(
    candidates: Iterable[Equipment],
    *,
    description: str | None,
    equipment_type: str | None,
    incoming_tag: str | None = None,
) -> Equipment | None:
    """Best-matching existing row whose description AND equipment type both
    fuzzy-match the incoming values, or ``None`` if nothing clears both
    thresholds.

    ``equipment_type`` may be absent — the PFD/P&ID/Vendor mappers don't
    extract it (only the Excel importer does). In that case we fall back
    to inferring a type from the incoming tag's prefix (the same heuristic
    already used when actually creating a new row via
    ``create_equipment_from_sync``), so the check still has a type signal
    to compare against. If inference also can't produce one, there's
    nothing to compare and this returns ``None`` — no duplicate check is
    possible without a type signal on either side, per design (both fields
    must match; we never fall back to description-only).
    """
    if is_blank(description):
        return None
    effective_type = equipment_type if not is_blank(equipment_type) else infer_equipment_type(incoming_tag)
    if is_blank(effective_type):
        return None

    best: Equipment | None = None
    best_score = 0.0
    seen_ids: set[int] = set()
    for eq in candidates:
        if eq.id in seen_ids:
            continue
        seen_ids.add(eq.id)
        if is_blank(eq.description) or is_blank(eq.equipment_type):
            continue
        d_sim = _similarity(eq.description, description)
        if d_sim < DESCRIPTION_THRESHOLD:
            continue
        t_sim = _type_similarity(eq.equipment_type, effective_type)
        if t_sim < TYPE_THRESHOLD:
            continue
        score = d_sim + t_sim
        if score > best_score:
            best_score = score
            best = eq
    return best


class DuplicatePair:
    __slots__ = ("a", "b", "description_similarity", "type_similarity")

    def __init__(self, a: Equipment, b: Equipment, description_similarity: float, type_similarity: float):
        self.a = a
        self.b = b
        self.description_similarity = description_similarity
        self.type_similarity = type_similarity


class _Prepared:
    __slots__ = ("eq", "desc", "desc_words", "etype", "type_words")

    def __init__(self, eq: Equipment):
        self.eq = eq
        self.desc = _normalize(eq.description)
        self.desc_words = _words(self.desc)
        self.etype = _normalize(eq.equipment_type)
        self.type_words = _words(self.etype)


def find_all_duplicate_pairs(equipment: Sequence[Equipment]) -> list[DuplicatePair]:
    """Pairwise scan across every row in ``equipment`` (expected: one
    project+workspace's worth) for description+type fuzzy matches — an
    on-demand audit of data that's already there, not tied to a sync
    event.

    Rows with a blank description or equipment type (missing, "-", "N/A",
    etc. — see ``is_blank``) are excluded entirely: a placeholder like "-"
    is not a real type value, and without ``is_blank`` two rows that both
    just have "-" for their type would score a perfect type match against
    each other, which is meaningless.

    O(n²) pairs, but each comparison is cheap: description/type strings
    and their word-sets are normalized ONCE per row up front (not
    recomputed per pair), and ``SequenceMatcher.quick_ratio()`` — a fast
    O(n) upper-bound estimate — gates the expensive O(n·m) ``ratio()``
    call, which the real one only ever gets past for actual candidates.
    Together these took a real ~550-row project from ~9s to well under 1s.
    If a project ever grows into the thousands of rows, move this to a
    background job instead of a synchronous request.

    Returns pairs sorted by combined similarity, highest first.
    """
    prepared = [
        _Prepared(e) for e in equipment
        if not is_blank(e.description) and not is_blank(e.equipment_type)
    ]
    pairs: list[DuplicatePair] = []
    n = len(prepared)
    for i in range(n):
        pa = prepared[i]
        for j in range(i + 1, n):
            pb = prepared[j]

            if _contrasting_words(pa.desc_words, pb.desc_words):
                continue
            desc_matcher = difflib.SequenceMatcher(None, pa.desc, pb.desc)
            if desc_matcher.quick_ratio() < DESCRIPTION_THRESHOLD:
                continue
            d_sim = desc_matcher.ratio()
            if d_sim < DESCRIPTION_THRESHOLD:
                continue

            if _contrasting_words(pa.type_words, pb.type_words):
                continue
            # Word-subset check mirrors _type_similarity's generic-vs-
            # specific relaxation (e.g. "Vessel" vs "Pressure Vessel").
            if pa.type_words <= pb.type_words or pb.type_words <= pa.type_words:
                t_sim = 0.95
            else:
                type_matcher = difflib.SequenceMatcher(None, pa.etype, pb.etype)
                if type_matcher.quick_ratio() < TYPE_THRESHOLD:
                    continue
                t_sim = type_matcher.ratio()
                if t_sim < TYPE_THRESHOLD:
                    continue

            pairs.append(DuplicatePair(pa.eq, pb.eq, d_sim, t_sim))
    pairs.sort(key=lambda p: p.description_similarity + p.type_similarity, reverse=True)
    return pairs
