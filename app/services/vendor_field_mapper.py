"""LLM-based vendor-data field mapper.

The vision extractor (``vision_pfd_service``) is deliberately format-agnostic:
it returns whatever JSON the LLM produces for each page of the PDF, with no
fixed schema. That's great for human review but means the project can't
auto-apply field updates the way it used to.

This module bridges that gap WITHOUT re-introducing format-specific code.
It has two enhancements beyond a plain "ask for these 15 fields" prompt:

1. Equipment-type awareness — before the mapper prompt runs, we cheaply
   sniff the vision JSON's title/description for keywords ("HEATER",
   "PUMP", "DRUM", "EXCHANGER", …) and pick a type-specific dimension
   guide. A pressure-vessel drawing's "L/T/T" means tangent-to-tangent
   length; a heater's "L" is usually overall projected length; a heat
   exchanger's is tube length. Feeding the model the RIGHT definition
   for the drawing in front of it dramatically reduces mis-picks like
   choosing "immersed length" when the reviewer wanted "overall length".

2. Confidence + evidence per field — the model returns not just the
   numeric value but ``{"value", "confidence", "source"}`` per field,
   plus a ``not_found_reason`` when a field is null. The frontend's
   file-detail page can show the reviewer *why* each value was chosen
   (or wasn't found), turning the mapper from a black box into an
   auditable step.

Backward compatibility: the top-level ``fields`` dict is unchanged —
it's still ``{field_name: str | None}`` — so ``sync_service`` and
``apply_update`` don't need any changes. Evidence is exposed as a NEW
``evidence`` sibling of ``fields`` that older callers can ignore.

Output shape::

    {
      "client_equipment_tag": str | None,
      "equipment_type_detected": str,          # NEW
      "fields": {                              # unchanged shape
        "absorbed_power_kw":   str | None,
        ...
      },
      "evidence": {                            # NEW, sibling of fields
        "absorbed_power_kw": {
          "confidence": "high" | "medium" | "low",
          "source":     str,                   # human-readable location
          "not_found_reason": str | None       # only set when value is null
        },
        ...
      }
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
    # Extra fields captured from vendor drawings — see corresponding
    # sections in the user prompt for extraction guidance.
    "length_overall_m",
    "mdmt_c",
    "hydrostatic_test_press_barg",
    "insulation",
]


# ---------------------------------------------------------------------------
# Equipment-type detection + type-specific dimension guidance
# ---------------------------------------------------------------------------
# The vision JSON always starts each page with an "overview" block whose
# text usually contains the drawing title / document title (e.g. "GENERAL
# ARRANGEMENT DRAWING FOR LP FLARE KNOCK OUT DRUM (V-S68105)"). Sniffing
# that text for a handful of keywords gets us the equipment type ~95% of
# the time for zero API cost.
#
# The map's KEYS are canonical type names we pass into the mapper prompt.
# Each entry lists trigger keywords (matched case-insensitively) and a
# dimension guide the mapper injects into the "Rules" section of the
# prompt so the LLM knows what "length_m" / "width_id_m" / "height_tt_m"
# actually mean for THIS type of drawing.

_TYPE_RULES: dict[str, dict[str, Any]] = {
    "vessel": {
        "keywords": [
            "vessel", "drum", "kod", "knock out", "knockout", "tank",
            "receiver", "separator", "accumulator", "surge", "flash",
        ],
        "length_m":    "tangent-to-tangent (T/T) length of the shell — the horizontal distance between the two head tangent lines on a horizontal drum.",
        "width_id_m":  "inside diameter (I.D.) of the shell.",
        "height_tt_m": "for a horizontal vessel: overall envelope height including saddles and platform. For a vertical vessel: T/T height between the two head tangent lines.",
    },
    "column": {
        "keywords": ["column", "tower", "absorber", "stripper", "distillation", "packed bed", "trayed"],
        "length_m":    "tangent-to-tangent (T/T) height of the column (same value as height_tt_m for a vertical column).",
        "width_id_m":  "inside diameter of the column shell.",
        "height_tt_m": "tangent-to-tangent height of the shell.",
    },
    "heater": {
        "keywords": [
            "heater", "electric heater", "immersion heater", "warmer", "fired heater",
            "reboiler", "reheater", "heating element",
        ],
        "length_m":    "overall projected length of the heater assembly (flange face to far end). NOT the immersed length, NOT the sensor length, NOT the withdrawal allowance — the total envelope length. If only sub-lengths are shown and no overall is stated, return null with a not_found_reason explaining what sub-lengths were seen.",
        "width_id_m":  "shell / vessel outside diameter, OR overall width in top view if it's a rectangular / flanged unit.",
        "height_tt_m": "overall stack height (for vertical heaters) or overall envelope height (for horizontal / skid-mounted heaters).",
    },
    "exchanger": {
        "keywords": [
            "exchanger", "heat exchanger", "hx", "shell and tube",
            "shell & tube", "plate", "condenser", "cooler", "chiller",
            "aircooler", "air cooler", "reboiler", "kettle", "u-tube",
        ],
        "length_m":    "tube length (front tubesheet face to rear tubesheet face) OR overall shell length between flanges.",
        "width_id_m":  "shell outside diameter (or overall width for plate / air-cooled types).",
        "height_tt_m": "overall envelope height including channel head, supports, and any bonnets.",
    },
    "pump": {
        "keywords": [
            "pump", "centrifugal pump", "positive displacement", "pd pump",
            "screw pump", "gear pump", "reciprocating", "canned",
        ],
        "length_m":    "baseplate overall length (drive end to opposite drive end, including motor and coupling).",
        "width_id_m":  "baseplate overall width.",
        "height_tt_m": "overall pump height including motor and any driver housing.",
    },
    "compressor": {
        "keywords": ["compressor", "blower", "fan", "gas turbine"],
        "length_m":    "skid overall length.",
        "width_id_m":  "skid overall width.",
        "height_tt_m": "skid overall height (top of highest component to base of skid).",
    },
    "package": {
        "keywords": ["package", "packaged", "skid", "unit", "system"],
        "length_m":    "skid overall length.",
        "width_id_m":  "skid overall width.",
        "height_tt_m": "skid overall height.",
    },
    "filter": {
        "keywords": ["filter", "strainer", "coalescer", "separator element"],
        "length_m":    "vessel tangent-to-tangent length OR housing overall length.",
        "width_id_m":  "housing outside diameter.",
        "height_tt_m": "overall envelope height including support.",
    },
    # Catch-all — no strong keyword hit. Give the model general guidance
    # and let it decide.
    "generic": {
        "keywords": [],
        "length_m":    "the drawing's OVERALL length (largest horizontal dimension shown). If multiple lengths are shown, prefer the labelled overall / total / T/T length.",
        "width_id_m":  "overall width or inside diameter — whichever the drawing explicitly labels as W or I.D.",
        "height_tt_m": "overall envelope height.",
    },
}


def _detect_equipment_type(vision_pages: list[dict[str, Any]]) -> str:
    """Heuristic sniff for equipment type from the vision JSON.

    Reads the ``overview`` block's raw string content (the vision pass
    produces per-page JSON where "overview" is either a string or a
    dict). Scores each candidate type by counting keyword hits across
    the concatenated overview text; returns the winner or "generic" if
    nothing scored.

    This is deliberately keyword-based, not an extra the LLM call — an
    extra API round-trip per PDF would double the mapper cost for a
    step the drawing title reliably answers on its own.
    """
    haystack_parts: list[str] = []
    for page in vision_pages:
        overview = page.get("overview")
        if overview is None:
            continue
        if isinstance(overview, str):
            haystack_parts.append(overview)
        elif isinstance(overview, dict):
            # Flatten one level of nested strings — vision output is
            # typically {"title": "...", "sections": [...]} or similar.
            for v in overview.values():
                if isinstance(v, str):
                    haystack_parts.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            haystack_parts.append(item)
    haystack = "\n".join(haystack_parts).lower()
    if not haystack:
        return "generic"

    # Two-pass scoring: first look at "specific" equipment types (vessel,
    # heater, exchanger, pump, compressor, column, filter). Only fall
    # through to the "package" wrapper type if NO specific type matched.
    # Without this split, "GAS COMPRESSOR PACKAGE — SKID K-S12000" would
    # score compressor=1 vs package=2 ("package" + "skid") and get
    # classified as a generic package, losing the compressor-specific
    # dimension guidance.
    _SPECIFIC = ("vessel", "column", "heater", "exchanger", "pump", "compressor", "filter")

    def _score(type_name: str) -> int:
        spec = _TYPE_RULES[type_name]
        return sum(
            # Word-boundary match so "heat exchanger" doesn't count as
            # a "heater" hit, and "package" doesn't get pulled by
            # "packaging" or other partials.
            1 for kw in spec["keywords"]
            if re.search(r"\b" + re.escape(kw) + r"\b", haystack)
        )

    best: str | None = None
    best_score = 0
    for type_name in _SPECIFIC:
        s = _score(type_name)
        if s > best_score:
            best_score = s
            best = type_name
    if best is not None:
        return best

    if _score("package") > 0:
        return "package"
    return "generic"


def _dimension_guide(equipment_type: str) -> str:
    """Return the ``length_m / width_id_m / height_tt_m`` block for the
    detected equipment type, formatted for injection into the mapper
    prompt. Falls back to the generic guide if the type isn't in the map.
    """
    spec = _TYPE_RULES.get(equipment_type) or _TYPE_RULES["generic"]
    return (
        f"   - length_m              For a {equipment_type}: {spec['length_m']}\n"
        f"                           Convert mm → m by dividing by 1000.\n"
        f"   - width_id_m            For a {equipment_type}: {spec['width_id_m']}\n"
        f"                           Convert mm → m by dividing by 1000.\n"
        f"   - height_tt_m           For a {equipment_type}: {spec['height_tt_m']}\n"
        f"                           Convert mm → m by dividing by 1000."
    )


MAPPER_SYSTEM_PROMPT = (
    "You map fields from a vendor data sheet or drawing into the project's "
    "Master Equipment List schema. Be precise. Return ONLY JSON, no markdown, "
    "no commentary. Report literal printed values converted into the "
    "requested units only when the conversion is explicitly required "
    "(kg→MT, mm→m). Never invent values. When a field is not stated on the "
    "drawing, return null and explain what was actually shown in "
    "not_found_reason — this is essential for the reviewer to understand "
    "why the field is empty."
)


def _build_user_prompt(vision_pages_json: str, equipment_type: str) -> str:
    """Build the mapper user prompt with type-specific dimension guidance.

    ``equipment_type`` is one of the canonical types in ``_TYPE_RULES``
    ("vessel", "heater", …) — the guide for L/W/H is inserted directly
    so the LLM has the correct definition for THIS drawing.
    """
    dim_guide = _dimension_guide(equipment_type)
    return f"""I'm giving you the JSON that came out of a vision pass over EVERY
page of a vendor data sheet PDF. Each page entry has an "overview" (full page)
and "tiles" (geometric slices for small text). The shape varies — adapt.

The document appears to describe a piece of equipment of type: **{equipment_type}**.
Interpret L / W / H accordingly using the guidance in the Rules section below.

From this JSON, find:

1. The equipment TAG this sheet describes (e.g. "V-S68105", "P-S37115A/B",
   "H-S67110"). Usually visible in the document title, title block, or a
   heading. Tags use LETTER-LETTER+DIGITS with optional /letter suffixes.

2. These values, if explicitly stated anywhere in the JSON:

   Core numerics (units must be converted as noted):
   - absorbed_power_kw     absorbed / operating / shaft power, in kW
   - rated_power_kw        rated / nameplate motor power, in kW
{dim_guide}
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
                           (e.g. "-40 / 120", "(-)40 / 120"). If the
                           drawing shows both a MIN DESIGN METAL TEMP
                           (MDMT) and a HOT design temp, put the HOT
                           side here and put the MDMT in mdmt_c below.
   - design_code           Design / certification code, e.g. "ASME VIII-1",
                           "ASME SECTION VIII DIV.1".
   - orientation           "Horizontal" / "Vertical" if explicitly stated.

   Extra fields (populate only if the drawing states them explicitly):
   - length_overall_m      The drawing's OVERALL length — flange-face to
                           flange-face INCLUDING heads / nozzles /
                           projections. Distinct from length_m above,
                           which is the T/T value. Some drawings show
                           BOTH labelled explicitly (e.g. "9212 (OVERALL
                           LENGTH)" and "7400 (TL-TL)"). If both are
                           shown, put T/T in length_m and OVERALL in
                           this field. If only ONE length dimension is
                           shown, put it in length_m and leave this
                           field null. Convert mm → m by dividing by
                           1000. Return a STRING.
   - mdmt_c                Minimum Design Metal Temperature, in °C.
                           Usually printed as a single negative number
                           on ASME VIII vessel drawings (e.g. "-40",
                           "-29"). Sometimes labelled "MIN DESIGN METAL
                           TEMP" or "MDMT". If DESIGN TEMPERATURE is
                           printed as a range like "-40 / 120", the
                           lower bound is the MDMT — return "-40" here
                           and put "120" in design_temp above (do NOT
                           duplicate the value in both fields).
   - hydrostatic_test_press_barg
                           Hydrostatic test pressure in barg. Shown on
                           every ASME VIII vessel drawing (e.g.
                           "HYDROSTATIC TEST PRESSURE: 14.847 barg").
                           Preserve the number as printed. Do NOT
                           convert units — if the drawing shows psi or
                           kPa, still return it as a string but include
                           the unit (e.g. "215 psi").
   - insulation            Free text combining insulation type +
                           thickness as printed on the drawing (e.g.
                           "40 mm personal protection",
                           "75 mm mineral wool + SS cladding",
                           "50 mm rockwool"). If the drawing only says
                           "insulated" with no thickness, return that
                           word. If the drawing shows NO insulation
                           spec, return null.

Rules:
- Convert kg→MT (divide by 1000, preserve precision: "38080 kg" → "38.08").
- Convert mm→m the same way ("6501 mm" → "6.501").
- If a value is given in the right unit already, keep it ("2.964").
- Return STRINGS for values (so ranges like "FV / 12" survive).
- Set a field's value to null if the JSON doesn't clearly state it. Never guess.
- The equipment tag is critical — if no plausible tag is visible, return null.

EXTRACTION POLICY — READ BEFORE FILLING ANY FIELD:

A. **Explicit label required.** For every numeric / dimensional field
   (length_m, width_id_m, height_tt_m, length_overall_m, mdmt_c,
   hydrostatic_test_press_barg, dry_weight_mt, operating_weight_mt,
   hydrotest_weight_mt, absorbed_power_kw, rated_power_kw, design_press,
   design_temp), you MUST see an explicit label on the drawing that
   matches the field's meaning before returning a value. Examples:

     - "OVERALL LENGTH: 9212 mm"                 → ✓ extract 9.212
     - "TL-TL: 7400" with explicit label         → ✓ extract 7.4
     - bare "795" in a revision cloud, no label  → ✗ return null
     - unlabelled dimension next to a nozzle     → ✗ return null
     - "IMMERSED LENGTH 724 mm" for a heater     → ✗ return null for
                                                    length_overall_m
                                                    (immersed ≠ overall)

   A dimension has to be UNAMBIGUOUSLY tied to the field's concept by
   a printed label. A bare number with no adjacent label — however
   plausible its magnitude — does NOT qualify. Every vendor draws
   differently; the label is the only thing that reliably identifies
   what a number means.

B. **Never guess magnitudes.** If two dimensions are shown but neither
   is clearly labelled with the concept, return null rather than picking
   the "bigger one" or the "one that looks right". A reviewer can add
   the value manually — a wrong auto-populated value is worse than an
   empty cell because it will silently propagate through weight totals,
   drawings referenced by tag, etc.

C. **Sub-lengths ≠ overall.** If the drawing shows only sub-lengths
   (immersed length, withdrawal allowance, thermal spacing, sensor
   length) and NO explicitly labelled overall / T/T length, return
   null for the primary length field with a not_found_reason listing
   the sub-lengths you saw. Do NOT sum them.

D. **Ranges stay whole.** If a field is a range on the drawing (e.g.
   DESIGN TEMP "-40 / +120"), return the range as a string preserving
   punctuation — never split into components unless the field's
   description explicitly asks for that (e.g. mdmt_c extracts just the
   lower bound; design_temp keeps just the upper bound).

For EACH field (whether the value is present or null), also record
evidence — this drives an auto-apply gate downstream:

  - confidence: "high"   → an explicit, unambiguous label matches the
                          field's concept. Example: "OVERALL LENGTH:
                          9212 mm" for length_overall_m. The value is
                          reliable enough to auto-apply.
                "medium" → labelled but the label is partial, indirect,
                          or requires interpretation (e.g. a title-block
                          entry "L: 6501" without units, or a note
                          referencing another sub-dimension). Still
                          auto-applied but flagged in the sync summary.
                "low"    → the value is present but the label is missing
                          or ambiguous. NOT auto-applied — held for
                          manual review. Use this whenever you're "kind
                          of sure but not certain".
  - source: a short phrase saying WHERE on the drawing you found this
            (e.g. "title block, DESIGN PRESSURE row", "top view —
            OVERALL LENGTH dimension line", "material list, SHELL row").
            Always include the LABEL text you matched against, not
            just the coordinate.
  - not_found_reason: when the value is null, briefly say what the
                      drawing DOES show for related dimensions (e.g.
                      "Shows immersed length 724, sensor length 550,
                      withdrawal 1500, and an unlabelled 795 in a
                      revision cloud, but no explicitly-labelled
                      overall length"). Reviewers use this to decide
                      whether to enter a value manually, and to know
                      whether the field is empty because the source
                      lacks it or because we chose not to guess.

Return ONLY this JSON object — no markdown fences, no commentary:

{{
  "client_equipment_tag": <string or null>,
  "equipment_type_detected": "{equipment_type}",
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
    "orientation":         <string or null>,
    "length_overall_m":    <string or null>,
    "mdmt_c":              <string or null>,
    "hydrostatic_test_press_barg": <string or null>,
    "insulation":          <string or null>
  }},
  "evidence": {{
    "absorbed_power_kw":   {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "rated_power_kw":      {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "length_m":            {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "width_id_m":          {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "height_tt_m":         {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "dry_weight_mt":       {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "operating_weight_mt": {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "hydrotest_weight_mt": {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "description":         {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "vendor":              {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "material":            {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "design_press":        {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "design_temp":         {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "design_code":         {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "orientation":         {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "length_overall_m":    {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "mdmt_c":              {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "hydrostatic_test_press_barg": {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}},
    "insulation":          {{"confidence": "high|medium|low", "source": <string>, "not_found_reason": <string or null>}}
  }}
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


def _normalize_evidence_entry(raw: Any) -> dict[str, Any]:
    """Guarantee the shape of one evidence dict. Missing keys become
    null / empty so the downstream UI can render without null-checking."""
    if not isinstance(raw, dict):
        return {"confidence": None, "source": None, "not_found_reason": None}
    conf = raw.get("confidence")
    if isinstance(conf, str):
        conf_norm = conf.strip().lower()
        if conf_norm not in ("high", "medium", "low"):
            conf_norm = None
    else:
        conf_norm = None
    src = raw.get("source")
    src_norm = str(src).strip() if src else None
    nfr = raw.get("not_found_reason")
    nfr_norm = str(nfr).strip() if nfr else None
    return {
        "confidence": conf_norm,
        "source": src_norm or None,
        "not_found_reason": nfr_norm or None,
    }


def map_vendor_fields(vision_pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send the raw vision JSON to the LLM and ask for the equipment tag,
    the 15 target MEL fields, and per-field evidence (confidence + source
    location + not-found reason).

    Returns ``None`` if the LLM call fails entirely; otherwise always
    returns the shape documented at the top of this module (fields the
    model couldn't find are set to null, and their evidence entries
    include a ``not_found_reason``).
    """
    if not is_enabled():
        return None

    equipment_type = _detect_equipment_type(vision_pages)
    log.info("vendor_field_mapper: detected equipment_type=%s", equipment_type)

    payload = json.dumps({"pages": vision_pages}, ensure_ascii=False)
    # Cap payload size — the vision JSON for a vendor sheet is rarely huge,
    # but vendor PDFs with many pages × many tiles can balloon. 200k chars is
    # comfortably within the LLM's context window without bloating cost.
    if len(payload) > 200_000:
        payload = payload[:200_000] + "\n...[truncated]"

    try:
        client = get_openai_client()
    except RuntimeError:
        log.warning("openai SDK not installed; vendor field mapping disabled")
        return None
    try:
        resp = client.chat.completions.create(
            model=settings.VISION_MODEL,
            # 2048 was tight even before evidence — 15 fields × ~3 lines
            # each was ~1400 tokens; adding evidence brings the payload
            # to ~2400. Bump to 4096 with headroom.
            max_tokens=4096,
            temperature=0,
            # Force JSON output at the API level — GPT-4o will retry
            # internally until it produces well-formed JSON.
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": MAPPER_SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(payload, equipment_type)},
            ],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Vendor field mapper call failed: %s", e)
        return None

    text = resp.choices[0].message.content or ""
    parsed = _parse_json(text) or {}

    # Normalize the shape — guarantee every target field is present (null is fine).
    tag_raw = parsed.get("client_equipment_tag")
    tag = (str(tag_raw).strip() or None) if tag_raw else None
    raw_fields = parsed.get("fields") or {}
    raw_evidence = parsed.get("evidence") or {}
    fields: dict[str, str | None] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for k in TARGET_FIELDS:
        # ---- Field value ----
        v = raw_fields.get(k)
        if v is None:
            fields[k] = None
        else:
            s = str(v).strip()
            fields[k] = s or None
        # ---- Evidence entry (present for every field, even when null) ----
        evidence[k] = _normalize_evidence_entry(raw_evidence.get(k))

    return {
        "client_equipment_tag": tag,
        "equipment_type_detected": equipment_type,
        "fields": fields,
        "evidence": evidence,
    }
