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


def _is_iso2(value: str) -> bool:
    return isinstance(value, str) and len(value) == 2 and value.isalpha() and value.isupper()


def _validate(data: dict) -> dict | None:
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    country = (data.get("country") or "").strip().upper()
    if not name or not description or not _is_iso2(country):
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
            if not isinstance(entry, str):
                return None
            code = entry.strip().upper()
            if not _is_iso2(code):
                return None
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
