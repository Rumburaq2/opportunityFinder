"""Phase 4a: LLM-backed structured extraction for free-text NGO posts.

Holds no source-specific knowledge — all source quirks live in the per-adapter
EXTRACTION_PROMPT. Returns a dict matching the `events` shape on success or
None when extraction / validation fails (the adapter then skips the item; it
reappears next cycle for retry).

Model: Gemini 2.5 Flash-Lite (free tier: 1k RPD). Structured output enforced
via response_schema. Post-LLM validation guards against the few failure modes
that schema alone can't catch (date ordering, dates far outside the realistic
window, malformed ISO-2 codes).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

from google import genai
from google.genai import types

_MODEL_NAME = "gemini-2.5-flash-lite"

# Activity formats the NGO adapters classify between. `other` covers ESC,
# Strategic Seminar, Job Shadowing, conferences, etc. — the adapter drops it.
FORMAT_YOUTH_EXCHANGE = "youth_exchange"
FORMAT_TRAINING_COURSE = "training_course"
FORMAT_OTHER = "other"
_ALLOWED_FORMATS = {FORMAT_YOUTH_EXCHANGE, FORMAT_TRAINING_COURSE, FORMAT_OTHER}

# Strict JSON shape the model must return. `nullable` lets partner_countries
# be omitted when the post doesn't list any (DiscoverEU-style single-country
# posts), so we don't force the model to invent data.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "name":               {"type": "string"},
        "country":            {"type": "string"},
        "period_start":       {"type": "string"},
        "period_end":         {"type": "string"},
        "partner_countries":  {
            "type": "array",
            "items": {"type": "string"},
            "nullable": True,
        },
        "description":        {"type": "string"},
        "format":             {
            "type": "string",
            "enum": list(_ALLOWED_FORMATS),
        },
    },
    "required": [
        "name",
        "country",
        "period_start",
        "period_end",
        "description",
        "format",
    ],
}

# Dates outside this window are almost certainly extraction errors (years
# mistaken for other numbers, OCR-style typos, etc.).
_DATE_WINDOW = timedelta(days=365 * 5)


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    _client = genai.Client(api_key=api_key)
    return _client


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# Real ISO-3166-1 alpha-2 country codes. We accept every actual country (so a
# training course in an "exotic" but real location is never dropped) but reject
# placeholder / bloc codes the LLM sometimes emits — e.g. "XX" (unknown) or
# "EU" — which pass a naive two-uppercase-letters shape check but aren't
# countries. "EU" is an ISO "exceptionally reserved" code, not a country, so it
# is deliberately absent; "XK" (Kosovo, user-assigned) IS included because
# SALTO and the Western-Balkans feeds list it.
_ISO_3166_1_ALPHA2 = frozenset((
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ "
    "BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ "
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ "
    "DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR "
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY "
    "HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP "
    "KE KG KH KI KM KN KP KR KW KY KZ "
    "LA LB LC LI LK LR LS LT LU LV LY "
    "MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ "
    "NA NC NE NF NG NI NL NO NP NR NU NZ OM "
    "PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW "
    "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ "
    "TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ "
    "UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW "
    "XK"
).split())

# Common LLM aliases for valid countries -> their canonical ISO code, applied
# before the set check so legitimate extractions aren't dropped over spelling.
_COUNTRY_ALIASES = {"UK": "GB", "EL": "GR"}


def _normalize_country(value: object) -> str | None:
    """Return a canonical ISO-3166-1 alpha-2 code, or None if `value` is not a
    recognised country (rejects placeholders like "XX" and bloc codes like
    "EU")."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    code = _COUNTRY_ALIASES.get(code, code)
    return code if code in _ISO_3166_1_ALPHA2 else None


def _validate(data: dict) -> dict | None:
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    country = _normalize_country(data.get("country"))
    if not name or not description or country is None:
        return None

    start = _parse_date(data.get("period_start", ""))
    end = _parse_date(data.get("period_end", ""))
    if start is None or end is None or start > end:
        return None

    today = date.today()
    if abs((start - today).days) > _DATE_WINDOW.days:
        return None
    if abs((end - today).days) > _DATE_WINDOW.days:
        return None

    partner_raw = data.get("partner_countries")
    partner: list[str] | None
    if partner_raw is None:
        partner = None
    else:
        if not isinstance(partner_raw, list):
            return None
        partner = []
        for entry in partner_raw:
            # Drop anything that isn't a real country (placeholders like "XX",
            # bloc codes like "EU") rather than rejecting the whole extraction —
            # one junk partner code shouldn't lose an otherwise-valid course.
            code = _normalize_country(entry)
            if code is None:
                continue
            if code != country and code not in partner:
                partner.append(code)
        if not partner:
            partner = None

    fmt = data.get("format")
    if fmt not in _ALLOWED_FORMATS:
        return None

    return {
        "name": name,
        "country": country,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "partner_countries": partner,
        "description": description,
        "format": fmt,
    }


def extract(
    prompt: str, content: str, pdf_bytes: bytes | None = None,
) -> dict | None:
    """Run the LLM against `content` using the caller-supplied `prompt`.

    If `pdf_bytes` is provided (an info-pack PDF for the same event), it's
    attached to the request as an inline PDF Part so Gemini can read its full
    content including tables and flag-icon layouts — typically the only place
    partner_countries and exact reimbursement / age limits appear.

    Returns a validated dict or None on any failure (network, model, parse,
    schema, validation). Callers must treat None as "skip this item for now".
    """
    parts: list = [prompt, content]
    if pdf_bytes is not None:
        parts.append(
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        )
    try:
        response = _get_client().models.generate_content(
            model=_MODEL_NAME,
            contents=parts,
            config={
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
                "temperature": 0,
            },
        )
    except Exception:
        logging.exception("llm_extractor: Gemini call failed")
        return None

    text = (response.text or "").strip() if response.text else ""
    if not text:
        logging.warning("llm_extractor: empty response from Gemini")
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logging.warning("llm_extractor: response was not valid JSON: %r", text[:200])
        return None

    validated = _validate(data)
    if validated is None:
        logging.info(
            "llm_extractor: validation rejected extraction (name=%r, country=%r, "
            "period=%r..%r)",
            data.get("name"), data.get("country"),
            data.get("period_start"), data.get("period_end"),
        )
    return validated
