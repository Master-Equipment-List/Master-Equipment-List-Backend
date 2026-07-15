"""LLM-based P&ID (Piping & Instrumentation Diagram) field mapper.

A P&ID is one notch more detailed than a PFD: it covers one (occasionally a few)
piece of equipment in detail, with every valve, instrument, line tag, and
nozzle drawn. For the MEL we don't ingest the full instrument/line/nozzle
detail (that belongs in separate registers — Line List, Instrument Index)
but we DO want the refined equipment-row fields a P&ID typically nails
better than a PFD:

  - description     more accurate / canonical wording
  - material        often more specific ("ASTM A516-70" instead of just "CS")
  - design_press    a single, board-approved value
  - design_temp     same
  - operating_press / operating_temp  if shown
  - configuration   parallel/standby pairing (1x100% / 2x100%)
  - design_code     usually called out in the title block or notes
  - orientation     sometimes spelled out, otherwise inferable from elevation views
  - insulation      type/thickness if printed
  - p_id            the P&ID document ID itself (so equipment row gets a back-link)

We also collect ancillary metadata that DOESN'T fit on the equipment row
but is useful for the file viewer / future Line+Instrument registers:

  - nozzle_schedule (per equipment)
  - line_tags
  - instrument_tags
  - notes

That ancillary data is returned but ignored by the apply path — it's stashed
on the extraction for human review.

Output shape:
{
  "equipment": [
    {
      "client_equipment_tag": "V-S67105",
      "fields": {  # writable to equipment row
        "description":     str|null,
        "material":        str|null,
        "operating_press": str|null,
        "operating_temp":  str|null,
        "design_press":    str|null,
        "design_temp":     str|null,
        "configuration":   str|null,
        "design_code":     str|null,
        "orientation":     str|null,
        "pid":             str|null,
      },
      "nozzles": [{"id": "N1", "size": "12\"", "rating": "300#", "service": "INLET"}, ...]
    },
    ...
  ],
  "line_tags":       [str, ...],   # informational, not applied
  "instrument_tags": [str, ...],   # informational, not applied
  "notes":           [str, ...],   # informational, not applied
}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.services._shared_client import get_openai_client

log = logging.getLogger(__name__)


# The seven MEL equipment-row fields a P&ID typically refines.
TARGET_FIELDS = [
    "description",
    "material",
    "operating_press",
    "operating_temp",
    "design_press",
    "design_temp",
    "configuration",
    "design_code",
    "orientation",
    "pid",
]


MAPPER_SYSTEM_PROMPT = (
    "You map equipment data from a P&ID (Piping & Instrumentation Diagram) "
    "into the project's Master Equipment List schema. Be precise. Return "
    "ONLY JSON, no markdown, no commentary. Preserve ranges and original "
    "punctuation exactly. Never invent values."
)


def _build_user_prompt(vision_pages_json: str) -> str:
    return f"""I'm giving you the JSON that came out of a vision pass over every
page of a P&ID. Each page entry has an "overview" (full page) and "tiles"
(geometric slices for small text). Adapt freely — the shape varies.

A P&ID covers one or a small number of equipment items in detail. Identify:

1. Every MAJOR EQUIPMENT TAG drawn (the named equipment, not symbol-only
   line callouts). Tags look like LETTER-LETTER+DIGITS with optional /letter
   suffixes (V-S67105, P-S67115A/B, H-S37110, etc.).

2. For each equipment tag, extract these MEL equipment-row fields when
   explicitly stated anywhere in the JSON. Set to null otherwise:

   - description     short equipment description (typically from the title
                     block, e.g. "HP FLARE KNOCK OUT DRUM")
   - material        material of construction (e.g. "LTCS + SS CLAD", "DSS",
                     "ASTM A516 Gr 70 + SS316L cladding")
   - operating_press operating pressure as printed (preserve ranges:
                     "0.1 / 3.5"). Just the pressure value(s), no "barg",
                     no "@".
   - operating_temp  operating temperature (preserve ranges: "(-)30 / 100").
   - design_press    design pressure (preserve "FV / 10").
   - design_temp     design temperature.
   - configuration   "<N>x<M>%" parallel-config token, e.g. "1x100%", "2x100%".
   - design_code     design / certification code, e.g. "ASME VIII-1".
   - orientation     "Horizontal" or "Vertical" if explicitly stated.
   - pid             the document ID of THIS P&ID drawing itself (read it
                     from the title block contractor doc ID). The same value
                     applies to every equipment on this sheet.

3. Also collect (as ancillary info, ONE level — not per-equipment):

   - nozzles per equipment:  list of {{id, size, rating, service}} from any
     nozzle schedule table.
   - line_tags:        list of distinct line-number strings printed on the
                       drawing (e.g. "6\\"-PG-1A1-1037", "10\\"-FG-2B3-1009").
   - instrument_tags:  list of instrument tag IDs (e.g. "FT-2510",
                       "LIC-67110-01"). Two-to-four letter prefix + digits.
   - notes:            preserve numbering ("1. ...", "2. ...").

Rules:
- Report ONLY what's literally on the page. Never invent.
- Preserve ranges and original punctuation.
- If you find a tag with no recoverable equipment-row fields (symbol only),
  still include it in `equipment` with all fields null (we use that signal
  to know the tag exists on this drawing).
- Return STRING values throughout.

Return ONLY this JSON object — no markdown fences, no commentary:

{{
  "equipment": [
    {{
      "client_equipment_tag": "<tag>",
      "fields": {{
        "description":     <string or null>,
        "material":        <string or null>,
        "operating_press": <string or null>,
        "operating_temp":  <string or null>,
        "design_press":    <string or null>,
        "design_temp":     <string or null>,
        "configuration":   <string or null>,
        "design_code":     <string or null>,
        "orientation":     <string or null>,
        "pid":             <string or null>
      }},
      "nozzles": [
        {{"id": <string>, "size": <string>, "rating": <string>, "service": <string>}}
      ]
    }}
  ],
  "line_tags":       [<string>, ...],
  "instrument_tags": [<string>, ...],
  "notes":           [<string>, ...]
}}

Here is the vision JSON:

{vision_pages_json}
"""


def is_enabled() -> bool:
    return bool(settings.OPENAI_API_KEY)


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


def map_pid_fields(vision_pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send the raw P&ID vision JSON to the LLM and ask for the equipment
    tag(s), refined MEL fields, plus ancillary line/instrument/nozzle data.

    Returns ``None`` only if the LLM call fails entirely; otherwise always
    returns the shape documented at the top of this file. Empty lists
    indicate "the document doesn't show that". Missing fields are null.
    """
    if not is_enabled():
        return None

    payload = json.dumps({"pages": vision_pages}, ensure_ascii=False)
    if len(payload) > 220_000:
        payload = payload[:220_000] + "\n...[truncated]"

    try:
        client = get_openai_client()
    except RuntimeError:
        log.warning("openai SDK not installed; P&ID field mapping disabled")
        return None
    try:
        resp = client.chat.completions.create(
            model=settings.VISION_MODEL,
            max_tokens=6144,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": MAPPER_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(payload)},
            ],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("P&ID field mapper call failed: %s", e)
        return None

    text = resp.choices[0].message.content or ""
    parsed = _parse_json(text) or {}

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
        nozzles = entry.get("nozzles") or []
        if not isinstance(nozzles, list):
            nozzles = []
        out_equipment.append({
            "client_equipment_tag": tag,
            "fields": fields,
            "nozzles": nozzles,
        })

    def _list_of_strings(key: str) -> list[str]:
        v = parsed.get(key) or []
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]

    return {
        "equipment":       out_equipment,
        "line_tags":       _list_of_strings("line_tags"),
        "instrument_tags": _list_of_strings("instrument_tags"),
        "notes":           _list_of_strings("notes"),
    }
