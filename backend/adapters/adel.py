"""ADEL Slovakia (adelslovakia.org) adapter: second Slovak NGO. Ingests youth
exchanges and training courses from the site's The Events Calendar (Tribe)
REST API.

Unlike every previous NGO source, ADEL exposes a structured JSON API:

    GET /wp-json/tribe/events/v1/events?per_page=50

which is strictly better than scraping the Elementor listing pages:

  - upcoming-only by default (past events need explicit date-window params),
    so there is no historical backlog to filter — SALTO-style server-side
    noise control for free;
  - stable numeric `id` → dedup id `adel:<id>`;
  - machine-readable `start_date` / `end_date` and `venue.city`/`.country`;
  - `categories` slugs pre-classify each event (`mladeznicke-vymeny` = YE,
    `treningy` = TC) — the LLM does no classification, we route by category;
  - `description` is the full detail-page HTML, which usually embeds the
    INFOPACK Google Drive link (pdf_fetcher already rewrites Drive share
    URLs) and names the participating countries in Slovak prose.

Per the locked ingestion rule the structured fields are parsed directly; the
LLM keeps a narrow job — `partner_countries` (info-pack PDF first, prose
second) and the English `description` summary. Dates and routing come from
the API; the host country is fed to the LLM as context ("Braga, Portugal")
and comes back normalised to ISO-2 through the shared validator.

robots.txt: general crawling allowed; Content-Signals `ai-train=no,
use=reference` are compatible (one-shot inference, output links back to the
call page). The API is an unversioned plugin endpoint — if ADEL drops the
plugin the adapter fails cleanly and other adapters are untouched.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from events_writer import eligible_countries_for, mark_skipped, seen_ids
from llm_extractor import extract
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "adel"

# Slovak NGO source — SK folded into every event's eligibility set (Phase
# 4f-B national-adapter regime); see eyc_breclav for the rationale.
SENDING_COUNTRY = "SK"

_API_URL = (
    "https://www.adelslovakia.org/wp-json/tribe/events/v1/events?per_page=50"
)
_ID_PREFIX = "adel:"

# Tribe category slug → events.source bucket. Events carrying neither slug
# (e.g. dobrovolnictvo, domace) are out of scope and skipped permanently.
_CATEGORY_TO_SOURCE = {
    "mladeznicke-vymeny": "youth_exchange",
    "treningy": "training_course",
}

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# First Drive share link in the description HTML is the INFOPACK button.
# Tracking junk after the file id (?fbclid=...) is harmless — pdf_fetcher
# extracts the id and rebuilds the direct-download URL.
_DRIVE_LINK_RE = re.compile(
    r"https://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^\"'\s<>]*"
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from a Slovak-language project
page of the NGO ADEL Slovakia (https://www.adelslovakia.org). The input
starts with a structured header (event type, exact dates, venue) taken from
the site's own calendar API — treat those header values as authoritative and
copy them into the corresponding fields. The prose after the header is the
project description in Slovak. An info-pack PDF is often attached to this
request — when present, prefer it for `partner_countries`; the participating
countries usually appear as a list or a group-leaders table near the start,
and practical details (travel reimbursement, age limits) near the end. When
no PDF is attached, extract from the prose alone; do NOT invent data.

Fields:
- `format`: copy the "Event type" value from the header verbatim
  ("youth_exchange" or "training_course"). Do not classify — the source has
  already guaranteed the type.
- `name`: the project title from the header, kept as-is (titles are already
  English); strip a trailing subtitle after a dash if it is long.
- `country`: ISO-3166 alpha-2 of the HOST country from the header's venue
  line (e.g. "Braga, Portugal" -> PT).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD), copied from the
  header's dates line.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list or group-leaders table).
    * SECONDARY SOURCE — the Slovak prose, which usually names the national
      teams inline, e.g. "tím z Portugalska, Španielska, Rumunska a
      Slovenska" — treat that as the participating list and drop the host.
      Slovak country names → Portugalsko=PT, Španielsko=ES, Rumunsko=RO,
      Slovensko=SK, Taliansko=IT, Nemecko=DE, Francúzsko=FR, Poľsko=PL,
      Česko/Česká republika=CZ, Maďarsko=HU, Bulharsko=BG, Grécko=GR,
      Chorvátsko=HR, Slovinsko=SI, Litva=LT, Lotyšsko=LV, Estónsko=EE,
      Fínsko=FI, Švédsko=SE, Dánsko=DK, Holandsko=NL, Belgicko=BE,
      Rakúsko=AT, Írsko=IE, Malta=MT, Cyprus=CY, Turecko=TR, Severné
      Macedónsko=MK, Nórsko=NO, Island=IS.
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder
  or bloc codes such as "XX", "EU", "EUR", or "INT". If the sources only say
  "programme countries" without naming any, return null rather than
  inventing codes. Return null only when neither source names any specific
  other country.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, host location, and anything practical (working language,
  participant age range, costs covered, how to apply). Use the page's own
  facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _fetch_api() -> list[dict] | None:
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(_API_URL)
    except httpx.HTTPError as exc:
        logging.warning("adel: GET failed url=%s err=%s", _API_URL, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "adel: non-200 url=%s status=%d", _API_URL, response.status_code,
        )
        return None
    try:
        return response.json().get("events") or []
    except ValueError as exc:
        logging.warning("adel: bad JSON from API err=%s", exc)
        return None


def _source_for(event: dict) -> str | None:
    """Map the event's Tribe categories to an events.source bucket, or None
    when the event is out of scope (volunteering, domestic, ...)."""
    for cat in event.get("categories") or []:
        source = _CATEGORY_TO_SOURCE.get(cat.get("slug") or "")
        if source:
            return source
    return None


def _info_pack_url(description_html: str) -> str | None:
    """First Google Drive share link in the description HTML — the INFOPACK
    button. None when the call has no info-pack (yet)."""
    m = _DRIVE_LINK_RE.search(description_html or "")
    return m.group(0) if m else None


def _llm_content(event: dict, source: str) -> str:
    """Authoritative API fields as a structured header, then the Slovak
    description prose with the HTML stripped."""
    venue = event.get("venue") or {}
    if isinstance(venue, list):  # Tribe returns [] when no venue is set
        venue = venue[0] if venue else {}
    venue_line = ", ".join(
        p for p in (venue.get("city"), venue.get("country")) if p
    ) or "(venue not set)"

    soup = BeautifulSoup(event.get("description") or "", "html.parser")
    prose = soup.get_text("\n", strip=True)

    return (
        f"Title: {html.unescape(event.get('title') or '')}\n"
        f"Event type: {source}\n"
        f"Dates: {(event.get('start_date') or '')[:10]} to "
        f"{(event.get('end_date') or '')[:10]}\n"
        f"Venue: {venue_line}\n"
        f"\n{prose}"
    )


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    events = _fetch_api()
    if events is None:
        return []
    if not events:
        logging.info("adel: API returned 0 upcoming events")
        return []

    candidates = {f"{_ID_PREFIX}{e['id']}": e for e in events if e.get("id")}
    seen = seen_ids(candidates.keys())
    fresh = {eid: e for eid, e in candidates.items() if eid not in seen}
    logging.info(
        "adel: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, event in fresh.items():
        source = _source_for(event)
        if source is None:
            slugs = ",".join(
                c.get("slug") or "?" for c in event.get("categories") or []
            )
            logging.info(
                "adel: skipping %s (out-of-scope categories: %s)",
                event_id, slugs or "none",
            )
            mark_skipped(event_id, ADAPTER_NAME, "category_out_of_scope")
            continue

        description_html = event.get("description") or ""
        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(description_html)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "adel: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "adel: no INFOPACK Drive link for %s (not published yet?)",
                event_id,
            )

        extracted = extract(
            EXTRACTION_PROMPT, _llm_content(event, source), pdf_bytes=pdf_bytes,
        )
        if extracted is None:
            continue  # validator already logged the reason

        # The API's dates are authoritative machine data — use them directly
        # rather than the LLM's copy (which the prompt asks to mirror anyway).
        period_start = (event.get("start_date") or "")[:10]
        period_end = (event.get("end_date") or "")[:10]
        try:
            end = datetime.strptime(period_end, "%Y-%m-%d").date()
        except ValueError:
            continue  # malformed API date — retry next cycle
        if end < today:
            # API is upcoming-only, but keep the backstop for consistency.
            logging.info(
                "adel: skipping %s (already ended on %s)", event_id, period_end,
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        items.append((source, {
            "id": event_id,
            "name": extracted["name"],
            "description": extracted["description"],
            "period_start": period_start,
            "period_end": period_end,
            "country": extracted["country"],
            "partner_countries": extracted["partner_countries"],
            "eligible_countries": eligible_countries_for(
                extracted["country"],
                extracted["partner_countries"],
                SENDING_COUNTRY,
            ),
            "url": event.get("url"),
            "raw": {
                "adel_id": event_id[len(_ID_PREFIX):],
                "categories": [
                    c.get("slug") for c in event.get("categories") or []
                ],
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("adel: returning %d items", len(items))
    return items
