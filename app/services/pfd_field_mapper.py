"""LLM-based PFD field mapper.

PFDs differ from vendor data sheets in one important way: each PFD describes
MANY equipment tags (the equipment header band at the top of the drawing
lists every piece of equipment in that system as columns). So instead of
returning ``{tag, fields}`` for one piece of equipment like the vendor
mapper does, this mapper returns ``{equipment: [{tag, fields}, ...]}`` —
one entry per column in the header band.

The seven target MEL fields a PFD typically populates (a project business
requirement, not a layout assumption):

  - description
  - configuration       (e.g. "1x100%", "2x100%")
  - operating_press     (operating pressure barg)
  - operating_temp      (operating temperature °C)
  - design_press        (design pressure barg)
  - design_temp         (design temperature °C)
  - material            (material of construction)

Values are strings (the equipment columns are TEXT in the DB so ranges
like ``"0.1 / 3.5"``, ``"FV / 10"``, ``"(-)40/120"`` survive). Missing
fields are null. The downstream ``apply_update`` then uses the
range-preservation heuristic in ``version_service`` to avoid shrinking a
richer existing Excel range to a single PFD-reported component.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


# Seven MEL columns updated from a PFD (per project spec).
TARGET_FIELDS = [
    "description",
    "configuration",
    "operating_press",
    "operating_temp",
    "design_press",
    "design_temp",
    "material",
]


MAPPER_SYSTEM_PROMPT = (
    "You map equipment data from a Process Flow Diagram (PFD) into the "
    "project's Master Equipment List schema. Be precise. Return ONLY JSON, "
    "no markdown, no commentary. Report literal printed values — preserve "
    "ranges (e.g. '0.1 / 3.5', 'FV / 10', '(-)40/120') and original "
    "punctuation. Never invent values."
)


def _build_user_prompt(vision_pages_json: str) -> str:
    return f"""I'm giving you the JSON that came out of a vision pass over every
page of a PFD (Process Flow Diagram). Each page entry has an "overview"
(full page) and "tiles" (geometric slices for small text). The shape varies
per document — adapt.

A PFD has an EQUIPMENT HEADER BAND, usually near the top of the drawing,
which is a horizontal table where each column is one piece of equipment.
Rows in that band are typically labelled EQUIPMENT (the tag), DESCRIPTION,
OPERATING (pressure / temperature), DESIGN (pressure / temperature),
MATERIAL.

From the vision JSON, find:

1. EVERY equipment column in the header band. Tags look like LETTER-LETTER+
   DIGITS with optional /letter suffixes (V-S67105, P-S67115A/B, H-S37110,
   A-S75110/120, etc.). Do not include tags drawn on diagram symbols only —
   only the columns of the header band.

2. For each tag, extract the seven MEL fields where they're explicitly
   stated in the JSON:

   - description     (DESCRIPTION row text, MINUS the "<N>x<M>%" config token)
   - configuration   (just the "<N>x<M>%" token from DESCRIPTION row, e.g. "1x100%")
   - operating_press (just the pressure portion of OPERATING; "0.1/3.5 barg @ 40°C"
                      → "0.1 / 3.5"; preserve ranges; no "barg", no "@")
   - operating_temp  (just the temperature portion of OPERATING; same rules)
   - design_press    (just the pressure portion of DESIGN; preserve "FV / 10")
   - design_temp     (just the temperature portion of DESIGN; preserve "(-)40 / 120")
   - material        (MATERIAL row text)

Rules:
- Preserve ranges and original punctuation exactly. "0.1 / 3.5" is more
  valuable than "3.5" — never collapse a range to one of its components.
- Set a field to null if the JSON doesn't clearly state it. Never guess.
- If a column has only a tag (no description, no operating, no material)
  it's probably a symbol-only column — skip it.
- Return STRINGS, not numbers, so ranges like "FV / 10" survive.

Return ONLY this JSON object — no markdown fences, no commentary:

{{
  "equipment": [
    {{
      "client_equipment_tag": "<tag>",
      "fields": {{
        "description":     <string or null>,
        "configuration":   <string or null>,
        "operating_press": <string or null>,
        "operating_temp":  <string or null>,
        "design_press":    <string or null>,
        "design_temp":     <string or null>,
        "material":        <string or null>
      }}
    }},
    ...
  ]
}}

Here is the vision JSON:

{vision_pages_json}
"""


def is_enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json(text: str) -> dict[str, Any] | None:
    text = _strip_json_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def map_pfd_fields(vision_pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send the raw vision JSON to Claude and ask for every equipment tag
    in the header band plus the seven target MEL fields per tag.

    Returns ``None`` if the LLM call fails entirely; otherwise always
    returns the shape documented at the top of this file. Fields the
    model couldn't find are set to null.
    """
    if not is_enabled():
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        log.warning("anthropic SDK not installed; PFD field mapping disabled")
        return None

    payload = json.dumps({"pages": vision_pages}, ensure_ascii=False)
    # PFD vision JSON can be larger than a vendor sheet (many tiles × many
    # tags); 200k chars is comfortably within Claude's context window.
    if len(payload) > 200_000:
        payload = payload[:200_000] + "\n...[truncated]"

    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=settings.VISION_MODEL,
            max_tokens=4096,
            temperature=0,
            system=[{
                "type": "text",
                "text": MAPPER_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [{"type": "text", "text": _build_user_prompt(payload)}],
            }],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("PFD field mapper call failed: %s", e)
        return None

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    parsed = _parse_json(text) or {}

    # Normalize the shape — guarantee every entry has the expected keys
    # and drop entries with no tag.
    out_equipment: list[dict[str, Any]] = []
    for entry in (parsed.get("equipment") or []):
        if not isinstance(entry, dict):
            continue
        tag_raw = entry.get("client_equipment_tag")
        if not tag_raw:
            continue
        tag = str(tag_raw).strip()
        if not tag:
            continue
        raw_fields = entry.get("fields") or {}
        fields: dict[str, str | None] = {}
        for k in TARGET_FIELDS:
            v = raw_fields.get(k)
            if v is None:
                fields[k] = None
                continue
            s = str(v).strip()
            fields[k] = s or None
        out_equipment.append({"client_equipment_tag": tag, "fields": fields})

    return {"equipment": out_equipment}
