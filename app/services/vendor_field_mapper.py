"""LLM-based vendor-data field mapper.

The vision extractor (``vision_pfd_service``) is deliberately format-agnostic:
it returns whatever JSON Claude produces for each page of the PDF, with no
fixed schema. That's great for human review but means the project can't
auto-apply field updates the way it used to.

This module bridges that gap WITHOUT re-introducing format-specific code:
it sends the raw vision JSON to Claude with a small, project-specific
prompt asking for ONLY the eight target fields the MEL system needs from
a vendor data sheet, plus the equipment tag the sheet refers to. The eight
fields are the actual MEL spreadsheet columns (a business requirement),
not assumed positions in any particular document layout.

Output shape:
    {
      "client_equipment_tag": str | None,
      "fields": {
        "absorbed_power_kw":     str | None,
        "rated_power_kw":        str | None,
        "length_m":              str | None,
        "width_id_m":            str | None,
        "height_tt_m":           str | None,
        "dry_weight_mt":         str | None,
        "operating_weight_mt":   str | None,
        "hydrotest_weight_mt":   str | None,
      }
    }

Values are strings (the equipment columns are TEXT in the DB, so they can
hold ranges like ``"FV/12"`` or ``"(-)29/150"``). Missing fields are null.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.services._shared_client import get_anthropic_client

log = logging.getLogger(__name__)


# MEL columns extractable from a vendor data sheet.
# The first 8 are dimensions/weights/power — the core vendor-specific values.
# The next group is "context" fields the vendor sheet usually also names:
# description, manufacturer, material, design conditions, design code.
# Having these in the mapper lets sync auto-CREATE an equipment row from
# a vendor PDF alone (when no Excel master list has been imported).
TARGET_FIELDS = [
    # Core vendor numerics
    "absorbed_power_kw",
    "rated_power_kw",
    "length_m",
    "width_id_m",
    "height_tt_m",
    "dry_weight_mt",
    "operating_weight_mt",
    "hydrotest_weight_mt",
    # Context — populated only if explicitly stated in the sheet
    "description",
    "vendor",
    "material",
    "design_press",
    "design_temp",
    "design_code",
    "orientation",
]


MAPPER_SYSTEM_PROMPT = (
    "You map fields from a vendor data sheet into the project's Master "
    "Equipment List schema. Be precise. Return ONLY JSON, no markdown, no "
    "commentary. Report literal printed values converted into the requested "
    "units only when the conversion is explicitly required (kg→MT, mm→m). "
    "Never invent values."
)


def _build_user_prompt(vision_pages_json: str) -> str:
    return f"""I'm giving you the JSON that came out of a vision pass over EVERY
page of a vendor data sheet PDF. Each page entry has an "overview" (full page)
and "tiles" (geometric slices for small text). The shape varies — adapt.

From this JSON, find:

1. The equipment TAG this sheet describes (e.g. "V-S68105", "P-S37115A/B",
   "H-S67110"). Usually visible in the document title, title block, or a
   heading. Tags use LETTER-LETTER+DIGITS with optional /letter suffixes.

2. These values, if explicitly stated anywhere in the JSON:

   Core numerics (units must be converted as noted):
   - absorbed_power_kw     absorbed / operating / shaft power, in kW
   - rated_power_kw        rated / nameplate motor power, in kW
   - length_m              equipment length (L) or tangent-to-tangent (T/T), in metres
   - width_id_m            width or inside diameter (I.D), in metres
   - height_tt_m           height (H) or tangent-to-tangent height, in metres
   - dry_weight_mt         empty / erection / dry weight, in metric tonnes (MT)
   - operating_weight_mt   operating weight, in MT
   - hydrotest_weight_mt   hydrotest / test weight, in MT

   Context (preserve as printed):
   - description           Short human description of the equipment
                           (typically the document title minus the tag).
                           E.g. "LP FLARE KNOCK OUT DRUM" from a doc titled
                           "GENERAL ARRANGEMENT DRAWING FOR LP FLARE KNOCK
                           OUT DRUM (V-S68105)".
   - vendor                The manufacturer / supplier name (from the
                           "CONSULTANT / VENDOR" block of the title block,
                           e.g. "VULCANIC", "HEATEC JIETONG").
   - material              Material of construction summary (from a
                           "MATERIAL" line or a bill-of-materials, e.g.
                           "SS + ALLOY 825 SHEATH", "DSS", "CS + SS CLAD").
   - design_press          Design pressure as printed, preserving ranges
                           (e.g. "10 / FV", "FV / 10").
   - design_temp           Design temperature as printed, preserving ranges
                           (e.g. "-40 / 120", "(-)40 / 120").
   - design_code           Design / certification code, e.g. "ASME VIII-1",
                           "ASME SECTION VIII DIV.1".
   - orientation           "Horizontal" / "Vertical" if explicitly stated.

Rules:
- Convert kg→MT (divide by 1000, preserve precision: "38080 kg" → "38.08").
- Convert mm→m the same way ("6501 mm" → "6.501").
- If a value is given in the right unit already, keep it ("2.964").
- Return STRINGS, not numbers (so ranges like "FV / 12" survive).
- Set a field to null if the JSON doesn't clearly state it. Never guess.
- The equipment tag is critical — if no plausible tag is visible, return null.

Return ONLY this JSON object — no markdown fences, no commentary:

{{
  "client_equipment_tag": <string or null>,
  "fields": {{
    "absorbed_power_kw":   <string or null>,
    "rated_power_kw":      <string or null>,
    "length_m":            <string or null>,
    "width_id_m":          <string or null>,
    "height_tt_m":         <string or null>,
    "dry_weight_mt":       <string or null>,
    "operating_weight_mt": <string or null>,
    "hydrotest_weight_mt": <string or null>,
    "description":         <string or null>,
    "vendor":              <string or null>,
    "material":            <string or null>,
    "design_press":        <string or null>,
    "design_temp":         <string or null>,
    "design_code":         <string or null>,
    "orientation":         <string or null>
  }}
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


def map_vendor_fields(vision_pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send the raw vision JSON to Claude and ask for the equipment tag + the
    eight target MEL fields. Returns ``None`` if the LLM call fails entirely;
    otherwise always returns the shape documented at the top of this file
    (fields the model couldn't find are set to null).
    """
    if not is_enabled():
        return None

    payload = json.dumps({"pages": vision_pages}, ensure_ascii=False)
    # Cap payload size — the vision JSON for a vendor sheet is rarely huge,
    # but vendor PDFs with many pages × many tiles can balloon. 200k chars is
    # comfortably within Claude's context window without bloating cost.
    if len(payload) > 200_000:
        payload = payload[:200_000] + "\n...[truncated]"

    try:
        client = get_anthropic_client()
    except RuntimeError:
        log.warning("anthropic SDK not installed; vendor field mapping disabled")
        return None
    try:
        resp = client.messages.create(
            model=settings.VISION_MODEL,
            max_tokens=2048,
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
        log.warning("Vendor field mapper call failed: %s", e)
        return None

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    parsed = _parse_json(text) or {}

    # Normalize the shape — guarantee every target field is present (null is fine).
    tag_raw = parsed.get("client_equipment_tag")
    tag = (str(tag_raw).strip() or None) if tag_raw else None
    raw_fields = parsed.get("fields") or {}
    fields: dict[str, str | None] = {}
    for k in TARGET_FIELDS:
        v = raw_fields.get(k)
        if v is None:
            fields[k] = None
            continue
        s = str(v).strip()
        fields[k] = s or None
    return {"client_equipment_tag": tag, "fields": fields}
