"""Youth Initiative Čaňa / YIC (yic.sk) adapter: fourth Slovak NGO. Ingests
open calls from the curated "OPEN CALL" category via the WordPress core REST
API.

Discovery generalises the ADEL lesson — probe `wp-json` before scraping:
yic.sk has no Tribe Events API, but the core API works and beats both RSS
(site-wide feed, capped at 10 mixed items) and scraping the /open-calls/
Elementor page:

    GET /wp-json/wp/v2/posts?categories=24&per_page=50

  - category 24 = "OPEN CALL", the curated bucket the /open-calls/ page
    mirrors; it holds every real call while the noisy siblings ("Youth
    exchange" = day-by-day diaries of past exchanges, "Nezaradené" =
    duplicates of properly-categorised calls) stay excluded — no LLM quota
    burned on retrospectives;
  - numeric post ids → dedup id `yic:<id>`;
  - full post body in `content.rendered` — one HTTP request per cycle, no
    detail-page fetches.

Unlike ADEL's Tribe API there are no event-date fields, so the LLM extracts
dates from the prose (EYC-style); the feed reaches back to 2025, so the
first run marks the ended backlog via the period_end backstop.

Info-packs are self-hosted uploads (`/wp-content/uploads/.../INFOPACK_*.pdf`)
on roughly half the posts — plain fetch, Drive links accepted as fallback.

robots.txt is fully open (empty Disallow, no content signals).

Returns (source, item) pairs so the caller can route each item to the right
events.source bucket.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

import httpx

from events_writer import eligible_countries_for, mark_skipped, seen_ids
from llm_extractor import (
    FORMAT_TRAINING_COURSE,
    FORMAT_YOUTH_EXCHANGE,
    extract,
)
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "yic"

# Slovak NGO source — SK folded into every event's eligibility set (Phase
# 4f-B national-adapter regime); see eyc_breclav for the rationale.
SENDING_COUNTRY = "SK"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

# Category 24 = "OPEN CALL" (curated; mirrors the /open-calls/ page).
_API_URL = "https://www.yic.sk/wp-json/wp/v2/posts?categories=24&per_page=50"
_ID_PREFIX = "yic:"

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Self-hosted info-pack PDFs (the usual case) and Drive share links (accepted
# as fallback should YIC switch hosting). Application forms (docs.google.com)
# are deliberately not matched.
_PDF_LINK_RE = re.compile(r"href=\"(https?://(?:www\.)?yic\.sk/[^\"]*\.pdf[^\"]*)\"")
_DRIVE_LINK_RE = re.compile(
    r"https://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^\"'\s<>]*"
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from an open-call post by the
Slovak NGO Youth Initiative Čaňa (https://www.yic.sk). Posts are mostly
Slovak (occasionally English) with emoji-labelled structured fields. An
info-pack PDF is often attached to this request — when present, prefer it
for `partner_countries`, `country` (host), and exact dates; the
participating countries usually appear as a list or a group-leaders/budget
table, and practical details (travel reimbursement, age limits) near the
end. When no PDF is attached, extract from the post text alone; do NOT
invent data.

DIGEST POSTS: some posts announce MULTIPLE distinct events at once (e.g. a
round-up listing a youth exchange AND a training course, each with its own
name and dates). In that case extract ONLY the FIRST event in the post,
completely and consistently — every field (name, dates, country, format,
description) must come from that one event. NEVER mix fields across events.
(The stored URL points at the full post, so readers still see the others.)

Classify the post (or, for a digest, its first event) into one of three
formats via the `format` field:
- "youth_exchange" — Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30 ("mládežnícka výmena", "youth exchange").
- "training_course" — Erasmus+ Key Action 1 training for youth workers /
  leaders / trainers ("tréningový kurz", "tréning", "školenie", "training
  course", "TC"). Target group is youth workers, NOT teen participants.
- "other" — everything else: study visits ("študijná návšteva"), European
  Solidarity Corps / ESC volunteering, job shadowing, seminars,
  conferences, solidarity projects; and any ONLINE-ONLY offering (webinars,
  e-learning, online courses) — no meaningful host country, not a KA1
  mobility. The extraction will be discarded.

Fields:
- `name`: short English title of the project — the proper project name
  (e.g. "Behind the Wheel", "GREEN DREAM"), dropping Slovak recruiting
  phrases and emoji around it.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens), usually in the "Miesto:" field (e.g. "Motycz Leśny
  n. Lublin, Poľsko" -> PL; "Bernāti, Lotyšsko" -> LV). Slovak country
  names → Poľsko=PL, Lotyšsko=LV, Litva=LT, Estónsko=EE, Taliansko=IT,
  Španielsko=ES, Nemecko=DE, Francúzsko=FR, Česko/Česká republika=CZ,
  Maďarsko=HU, Rumunsko=RO, Bulharsko=BG, Grécko=GR, Portugalsko=PT,
  Chorvátsko=HR, Slovinsko=SI, Fínsko=FI, Švédsko=SE, Dánsko=DK,
  Holandsko=NL, Belgicko=BE, Rakúsko=AT, Írsko=IE, Malta=MT, Cyprus=CY,
  Slovensko=SK, Turecko=TR, Severné Macedónsko=MK, Gruzínsko=GE,
  Nórsko=NO, Island=IS.
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD) of the ACTIVITY
  itself, from "Termín konania:" — formats like "19. – 25. júl 2026" or
  "17.08.2026 – 25.08.2026". Slovak month names: januára/január=01,
  februára/február=02, marca/marec=03, apríla/apríl=04, mája/máj=05,
  júna/jún=06, júla/júl=07, augusta/august=08, septembra/september=09,
  októbra/október=10, novembra/november=11, decembra/december=12. Use the
  core activity dates, NOT the arrival/departure days ("Príchod",
  "Odchod") and NOT the application deadline. If the year is missing on
  the start side, copy it from the end side.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list, group-leaders table, or budget/reimbursement table with one row
      per sending country).
    * SECONDARY SOURCE — the post prose, where countries are actually
      NAMED. A participant count alone ("25 účastníkov") is NOT a country
      list.
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder
  or bloc codes such as "XX", "EU", "EUR", or "INT". If the sources only
  count participants or say "programme countries" without naming
  countries, return null rather than inventing codes.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, host location, and anything practical (working language,
  participant age range, costs covered, number of Slovak places, how to
  apply). Use the post's own facts; do not embellish.

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
        logging.warning("yic: GET failed url=%s err=%s", _API_URL, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "yic: non-200 url=%s status=%d", _API_URL, response.status_code,
        )
        return None
    try:
        posts = response.json()
    except ValueError as exc:
        logging.warning("yic: bad JSON from API err=%s", exc)
        return None
    return posts if isinstance(posts, list) else None


def _info_pack_url(body_html: str) -> str | None:
    """Info-pack link in the post body: self-hosted yic.sk PDFs first
    (prefer filenames containing 'infopack'), Drive share links as fallback.
    None → text-only extraction."""
    pdfs = _PDF_LINK_RE.findall(body_html or "")
    for url in pdfs:
        if "infopack" in url.lower():
            return url
    if pdfs:
        return pdfs[0]
    m = _DRIVE_LINK_RE.search(body_html or "")
    return m.group(0) if m else None


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    posts = _fetch_api()
    if posts is None:
        return []
    if not posts:
        logging.info("yic: API returned 0 open-call posts")
        return []

    candidates = {f"{_ID_PREFIX}{p['id']}": p for p in posts if p.get("id")}
    seen = seen_ids(candidates.keys())
    fresh = {eid: p for eid, p in candidates.items() if eid not in seen}
    logging.info(
        "yic: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, post in fresh.items():
        body = (post.get("content") or {}).get("rendered") or ""
        if not body.strip():
            logging.info("yic: skipping %s (empty body)", event_id)
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(body)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "yic: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info("yic: no info-pack link for %s", event_id)

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "yic: skipping %s (format=%s, name=%r)",
                event_id, fmt, extracted["name"],
            )
            mark_skipped(event_id, ADAPTER_NAME, f"format_{fmt}")
            continue

        try:
            end = datetime.strptime(extracted["period_end"], "%Y-%m-%d").date()
        except ValueError:
            continue  # transient parse failure — retry next cycle
        if end < today:
            logging.info(
                "yic: skipping %s (already ended on %s)",
                event_id, extracted["period_end"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        items.append((source, {
            "id": event_id,
            "name": extracted["name"],
            "description": extracted["description"],
            "period_start": extracted["period_start"],
            "period_end": extracted["period_end"],
            "country": extracted["country"],
            "partner_countries": extracted["partner_countries"],
            "eligible_countries": eligible_countries_for(
                extracted["country"],
                extracted["partner_countries"],
                SENDING_COUNTRY,
            ),
            "url": post.get("link"),
            "raw": {
                "yic_id": event_id[len(_ID_PREFIX):],
                "wp_title": (post.get("title") or {}).get("rendered"),
                "wp_date": post.get("date"),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("yic: returning %d items", len(items))
    return items
