"""SALTO European Training Calendar adapter: ingests Erasmus+ youth-worker
Training Courses from SALTO-YOUTH's official pan-European directory at
https://www.salto-youth.net/tools/european-training-calendar/ .

Unlike the Czech NGO adapters this is an English-language, field-structured,
official aggregator covering all programme countries. Two properties let us
lean on the server and skip the usual classification step:

  * `b_activity_type=4` filters the listing to Training Courses only, so every
    ingested item belongs in the `training_course` bucket. The LLM does NO
    classification here — `format` is hard-set to `training_course` and the
    server-side activity-type filter is treated as authoritative.
  * `b_begin_date_after_*` and `b_application_deadline_after_*` (both set to
    *today*, generated per run) make the server return only future events whose
    application window is still open, so closed-deadline noise never enters.

robots.txt forbids the pagination param (`Disallow: /*?*b_offset*`), so we
NEVER emit `b_offset`. Instead we sort newest-first (`b_order=creation`) and
raise `b_limit` to pull the whole current backlog in one robots-compliant
request; new offers surface at the top on later cycles and hourly dedup
(`seen_ids`) catches them.

Eligibility is the `b_participating_countries` filter — a repeatable, OR-logic
param. Phase 4f-A ships with a single value (CZ) so only courses a Czech
participant can join are ingested (zero dispatch changes, zero noise for the
current Czech-only audience). Phase 4f-B widens `_PARTICIPATING_COUNTRIES` to
the full programme-country set and adds a per-user home-country eligibility
gate in the dispatcher — no adapter rewrite, just a longer constant.

Flow per hourly cycle (mirrors the `bfy` HTML-listing pattern):
  1. GET the listing (page 1 only, newest-first); parse anchors to
     /training/<slug>.<numericid>/ and build candidate ids `salto:<numericid>`.
  2. Ask events_writer which ids are already seen and drop those (no LLM call
     for unchanged offers).
  3. For each new id, GET its detail page, extract the event-detail block,
     find the SALTO-hosted info-pack PDF (Canva links are skipped, like bfy),
     and pass body + PDF to llm_extractor with the bespoke English prompt.
  4. Route every result to the `training_course` bucket; drop
     `period_end < today` as a backstop.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from events_writer import mark_skipped, seen_ids
from llm_extractor import extract
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "salto"

# Every item is a Training Course (guaranteed by b_activity_type=4), so there
# is no classification step — all results route to this one bucket.
_SOURCE = "training_course"

_BASE = "https://www.salto-youth.net"
_BROWSE_URL = f"{_BASE}/tools/european-training-calendar/browse/"
_ID_PREFIX = "salto:"

# SALTO activity-type id 4 == "Training Course".
_ACTIVITY_TYPE_TRAINING_COURSE = "4"
# Phase 4f-A: CZ only. Phase 4f-B widens this to the full programme-country set
# (the param is repeatable with OR logic, so one feed still covers all of them).
_PARTICIPATING_COUNTRIES = ("CZ",)
# robots.txt forbids b_offset, so we crawl page 1 only and raise the limit to
# capture the whole current backlog in a single compliant request.
_LISTING_LIMIT = "100"

# Detail URLs look like /tools/european-training-calendar/training/<slug>.<id>/
# where <id> is a stable numeric id we use for dedup.
_DETAIL_RE = re.compile(
    r"/tools/european-training-calendar/training/[a-z0-9-]+\.(\d+)/?",
)

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ Training Course from an English-language event
page on the SALTO-YOUTH European Training Calendar
(https://www.salto-youth.net). The page is field-structured English prose: a
title, an activity-type label ("Training Course"), a "<dates> | <City>,
<Country>" line, and several paragraphs of description. An info-pack PDF is
often attached to this request — when present, prefer it for
`partner_countries`, `country` (host), and exact dates. When no PDF is
attached, extract from the page text alone; do NOT invent data.

Every offer in this feed is an Erasmus+ Key Action 1 Training Course for youth
workers / trainers / youth leaders. Always set `format` to "training_course".
Do not classify — the source has already guaranteed the type.

Fields:
- `name`: the course title as shown, trimmed of any trailing edition/subtitle
  noise. Keep it in English (titles are already English; if a title is in
  another language with an English part, prefer the English part).
- `country`: ISO-3166 alpha-2 of the HOST country — the country in the
  "<dates> | <City>, <Country>" venue line, where the course physically takes
  place (e.g. "Pińczów, Poland" -> PL).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). The venue line writes
  the range like "13-22 July 2026" or "28 September - 4 October 2026". If the
  month or year is missing on the start side, copy it from the end side.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached. Look for a
      "Participating countries" / "Partner organisations" / "Countries
      involved" list or a group-leaders table with one row per country.
    * SECONDARY SOURCE — the page text. The description almost always names
      them inline, e.g. "brings together participants from Poland, Croatia,
      Italy, Portugal, Romania, and Cyprus" — treat that sentence as the
      participating list and drop the host.
  Return null only when neither source names any other country.
- `description`: 80-160 word English summary covering the topic, target group
  (youth workers / trainers / etc.), dates, host location, and anything
  practical (working language, methods, what participants will do). Use the
  page's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best guess —
post-validation will drop obviously broken extractions.
"""


def _listing_url() -> str:
    """Build the browse URL with today's date filters. Deliberately omits
    b_offset (robots-disallowed) and sorts newest-first so page 1 + a high
    b_limit captures the whole open backlog."""
    today = date.today()
    params: list[tuple[str, str]] = [
        ("b_activity_type", _ACTIVITY_TYPE_TRAINING_COURSE),
        ("b_order", "creation"),
        ("b_limit", _LISTING_LIMIT),
        ("b_begin_date_after_day", str(today.day)),
        ("b_begin_date_after_month", str(today.month)),
        ("b_begin_date_after_year", str(today.year)),
        ("b_application_deadline_after_day", str(today.day)),
        ("b_application_deadline_after_month", str(today.month)),
        ("b_application_deadline_after_year", str(today.year)),
    ]
    for code in _PARTICIPATING_COUNTRIES:
        params.append(("b_participating_countries", code))
    return str(httpx.URL(_BROWSE_URL, params=params))


def _http_get(url: str) -> str | None:
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logging.warning("salto: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "salto: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    return response.text


def _discover(listing_html: str) -> list[tuple[str, str]]:
    """Return ordered, de-duplicated (numeric_id, detail_url) pairs found on
    the listing page."""
    soup = BeautifulSoup(listing_html, "html.parser")
    seen: set[str] = set()
    ordered: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        m = _DETAIL_RE.search(href)
        if not m:
            continue
        numeric_id = m.group(1)
        if numeric_id in seen:
            continue
        seen.add(numeric_id)
        # Normalize to the canonical absolute detail URL (drop query/fragment).
        path = urlparse(urljoin(_BASE, href)).path
        ordered.append((numeric_id, f"{_BASE}{path}"))
    return ordered


def _body_text(detail_html: str) -> str:
    """Return the event-detail block text only. SALTO wraps the actual offer in
    div.tool-item-detail-wrapper; selecting it keeps the site-wide nav menu out
    of the LLM input (the generic main/article fallback pulls the whole nav)."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    main = (
        soup.find("div", class_="tool-item-detail-wrapper")
        or soup.find("main")
        or soup.find("article")
        or soup
    )
    return main.get_text("\n", strip=True)


def _info_pack_url(detail_html: str) -> str | None:
    """Find the SALTO-hosted info-pack PDF under the calendar's /download/
    path. Canva 'Infopack can be found here' links are skipped (can't fetch,
    can't render) — same policy as the bfy adapter."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href:
            continue
        url = urljoin(_BASE, href)
        if urlparse(url).hostname not in ("www.salto-youth.net", "salto-youth.net"):
            continue
        path = urlparse(url).path.lower()
        if "/european-training-calendar/download/" in path and path.endswith(".pdf"):
            return url
    return None


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events. `source`
    is always "training_course" — the listing is pre-filtered to that type."""
    listing = _http_get(_listing_url())
    if not listing:
        logging.info("salto: listing fetch failed")
        return []

    discovered = _discover(listing)
    if not discovered:
        logging.info("salto: listing returned 0 training offers")
        return []

    candidates = {f"{_ID_PREFIX}{nid}": url for nid, url in discovered}
    seen = seen_ids(candidates.keys())
    fresh = {eid: url for eid, url in candidates.items() if eid not in seen}
    logging.info(
        "salto: %d listed, %d already seen, %d to extract",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, detail_url in fresh.items():
        detail_html = _http_get(detail_url)
        if not detail_html:
            continue  # transient — retry next cycle

        body = _body_text(detail_html)
        if not body.strip():
            logging.info("salto: skipping %s (empty body)", event_id)
            continue

        info_pack = _info_pack_url(detail_html)
        pdf_bytes: bytes | None = None
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "salto: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "salto: no SALTO-hosted info-pack PDF for %s (Canva or absent)",
                event_id,
            )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        try:
            end = datetime.strptime(extracted["period_end"], "%Y-%m-%d").date()
        except ValueError:
            continue  # transient parse failure — retry next cycle
        if end < today:
            # Server already filters past events, but keep the backstop so a
            # date-parse quirk can't leak an ended course into notifications.
            logging.info(
                "salto: skipping %s (already ended on %s)",
                event_id, extracted["period_end"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        items.append((_SOURCE, {
            "id": event_id,
            "name": extracted["name"],
            "description": extracted["description"],
            "period_start": extracted["period_start"],
            "period_end": extracted["period_end"],
            "country": extracted["country"],
            "partner_countries": extracted["partner_countries"],
            "url": detail_url,
            "raw": {
                "salto_id": event_id[len(_ID_PREFIX):],
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("salto: returning %d items", len(items))
    return items
