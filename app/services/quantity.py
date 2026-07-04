"""Compute TOTAL weight columns from per-unit weight × configuration count.

Background
----------
The reference EPC MEL workbook has FOUR weight columns:

  DRY WT (per unit)        — weight of a single equipment item
  OPE WT (per unit)
  TOTAL DRY WT (installed) — weight actually carried on the platform,
  TOTAL OPE WT (installed)   i.e. per-unit × how-many-are-installed.

Where the count comes from the CONFIGURATION column ("1 x 100%",
"2 x 100%", "2 x 50%", "1 no.", "2 no's.", …).

Verified against the reference workbook — every row where DRY and
TOTAL DRY both have values, the ratio equals the leading integer in
the CONFIGURATION string. Examples:

    1 x 100%  →  DRY 38.0    → TOTAL 38.0    (×1)
    2 x 100%  →  DRY 1.035   → TOTAL 2.07    (×2)
    2 x 50%   →  DRY 4.567   → TOTAL 9.134   (×2)

Usage
-----
Callers pick the value they render / save:

    stored_total = eq.total_dry_weight_mt
    computed_total = compute_installed_weight(
        eq.dry_weight_mt, eq.configuration,
    )
    effective_total = pick_effective_total(stored_total, computed_total)

``pick_effective_total`` prefers the STORED value when it exists so
users who deliberately override the computed number (rare — usually
for "standby unit not counted" cases) don't have their entry
overwritten. When the stored value is missing / blank / "-", the
computed value fills the gap.
"""
from __future__ import annotations

import re


# Matches the first integer in the CONFIGURATION string. Deliberately
# permissive so it accepts every variant we've seen in the source
# workbooks: "1 x 100%", "2x100%", "1 no.", "2 no's.", "3 nos", etc.
_COUNT_RE = re.compile(r"\d+")

# Common human-blank placeholders. Any of these mean "no value stored"
# and should be treated the same as None when deciding whether to
# substitute a computed total.
_BLANKS = {"", "-", "—", "–", "n/a", "na", "none"}


def unit_count_from_configuration(configuration: str | None) -> int | None:
    """Return the installed-unit count from a CONFIGURATION string.

    >>> unit_count_from_configuration("1 x 100%")
    1
    >>> unit_count_from_configuration("2 x 100%")
    2
    >>> unit_count_from_configuration("2 x 50%")
    2
    >>> unit_count_from_configuration("1 no.")
    1
    >>> unit_count_from_configuration("2 no's.")
    2
    >>> unit_count_from_configuration("Part of package") is None
    True
    >>> unit_count_from_configuration(None) is None
    True
    >>> unit_count_from_configuration("") is None
    True
    """
    if not configuration:
        return None
    m = _COUNT_RE.search(str(configuration))
    if not m:
        return None
    n = int(m.group(0))
    # Guard against absurd values (e.g. someone typing a percentage-only
    # config where the regex would grab "100"). Real installed counts
    # are almost always ≤ 10; capping at 100 leaves headroom without
    # accepting garbage.
    if n < 1 or n > 100:
        return None
    return n


def _parse_number(value: str | None) -> float | None:
    """Parse an equipment weight value into a float.

    The MEL stores weights as strings so ranges like ``"FV / 12"``
    survive verbatim through the pipeline. Weights that are ranges
    aren't multiplyable, so this returns None in that case (the
    caller then falls back to whatever is stored, or a dash).
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _BLANKS:
        return None
    if "/" in s:
        # Range like "0.1 / 3.5" — refuses to compute a "total" from it.
        return None
    # Tolerate stray commas as thousands separators ("1,234.5").
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _format_weight(value: float) -> str:
    """Format a computed float weight back to a string.

    Trims trailing zeros so ``2.070000`` renders as ``"2.07"`` — matches
    the reference workbook's convention where weights carry their
    natural precision without padding.
    """
    # Round to 6 decimals to erase float-arithmetic noise
    # (``1.035 * 2 → 2.0700000000000003``).
    s = f"{value:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def compute_installed_weight(
    per_unit_value: str | None, configuration: str | None,
) -> str | None:
    """Compute ``per_unit × count(configuration)`` and return a formatted
    string, or None if the inputs don't allow a clean multiplication.
    """
    per_unit = _parse_number(per_unit_value)
    if per_unit is None:
        return None
    count = unit_count_from_configuration(configuration)
    if count is None:
        return None
    return _format_weight(per_unit * count)


def is_blank(value: str | None) -> bool:
    """True when ``value`` is missing or one of the human-blank
    placeholders ("-", "—", "n/a", …).
    """
    if value is None:
        return True
    return str(value).strip().lower() in _BLANKS


def pick_effective_total(
    stored_total: str | None, computed_total: str | None,
) -> str | None:
    """Return the total that should be shown / exported.

    Prefers the STORED value when it exists and isn't a blank
    placeholder, so users who deliberately override (rare — usually
    "standby unit not counted in operating weight") keep their entry.
    Falls back to the computed value otherwise.
    """
    if not is_blank(stored_total):
        return stored_total
    return computed_total
