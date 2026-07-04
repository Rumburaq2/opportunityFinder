"""Youthfully Yours SK (youthfullyyours.sk) adapter: first Slovak NGO. Ingests
open calls from the "Príležitosti" opportunities page.

The site's RSS feeds are a trap: /sk/feed/ and the category feeds carry
past-event participant write-ups ("Zorganizovali sme" retrospectives), not open
calls, and have no content:encoded. The actual open calls are a WordPress
custom post type at /prilezitosti/<slug>/ (no /sk/ prefix, no feed of its own),
rendered server-side onto /sk/prilezitosti/ as cards: a poster image and an
application deadline ("Termín prihlášky: DD/MM/YYYY"), current calls first,
then every past call under a "Predchádzajúce príležitosti" divider.

Flow per hourly cycle:

  1. GET the listing page; parse the cards into (slug, deadline) pairs.
  2. Compute candidate ids `yysk:<slug>`; drop ids events_writer already knows
     (no LLM call for unchanged posts).
  3. Deadline pre-filter: a card whose application deadline has passed is
     marked skipped WITHOUT fetching its detail page — the listing keeps all
     past calls forever, so this is what keeps the backlog out of Gemini.
  4. For each remaining id, GET the detail page, extract the body text, and
     look for a self-hosted info-pack PDF (present on some calls, absent on
     others — the poster image is not a PDF). Site-wide nav/menu PDFs (e.g.
     the HILL Manifesto) are structurally excluded, not blocklisted.
  5. Pass body (and PDF when present) to llm_extractor with the bespoke
     prompt. Pages are mostly English with Slovak field labels. Drop items
     where `format == 'other'` or `period_end < today`.

Returns (source, item) pairs so the caller can route each item to the right
events.source bucket.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from events_writer import eligible_countries_for, mark_skipped, seen_ids
from llm_extractor import (
    FORMAT_TRAINING_COURSE,
    FORMAT_YOUTH_EXCHANGE,
    extract,
)
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "yysk"

# Slovak NGO source — SK folded into every event's eligibility set (Phase
# 4f-B national-adapter regime); see eyc_breclav for the rationale. YYSK
# occasionally recruits for other sending countries too ("Ideal Participant
# profile: ... Czech Nationality") — the prompt folds those into
# partner_countries so they land in the eligible set as well.
SENDING_COUNTRY = "SK"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

_BASE = "https://youthfullyyours.sk"
_LISTING_URL = f"{_BASE}/sk/prilezitosti/"
_ID_PREFIX = "yysk:"

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Opportunity URLs look like /prilezitosti/<slug>/ (NOT under /sk/).
_OPPORTUNITY_PATH_RE = re.compile(r"^/prilezitosti/([a-z0-9][a-z0-9-]*)/?$")

# Card deadline text: "Termín prihlášky: 10/07/2026" (DD/MM/YYYY).
_DEADLINE_RE = re.compile(r"Termín prihlášky:\s*(\d{1,2})/(\d{1,2})/(\d{4})")


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from an open-call page of the
Slovak NGO Youthfully Yours SK (https://youthfullyyours.sk). The page body is
mostly English, field-structured prose; occasional labels are Slovak ("Termín
prihlášky" = application deadline, "Miesto konania" = venue). An info-pack PDF
may be attached to this request — when present, prefer it for
`partner_countries`, `country` (host), and exact dates; the participating
countries usually appear as a list or a group-leaders table near the start,
and practical details (travel reimbursement, age limits) near the end. When
no PDF is attached, extract from the page text alone; do NOT invent data.

Classify the post into one of three formats via the `format` field:
- "youth_exchange" — Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30. Titles are usually prefixed "YE". An "APV+YE"
  post recruits for the youth exchange (the APV is a side detail) — classify
  it as youth_exchange.
- "training_course" — Erasmus+ Key Action 1 training for youth workers /
  leaders / trainers. Titles are usually prefixed "TC". Target group is
  youth workers, NOT teen participants.
- "other" — everything else: European Solidarity Corps / ESC volunteering,
  job shadowing, study visits, seminars, conferences, standalone APV
  announcements that recruit nobody. The extraction will be discarded.

The title prefix (YE / TC) is a strong hint but not authoritative — use the
page's own description of the target group to classify.

The page typically uses these field patterns — look for them first:
- "Project Organiser:" — the foreign organising NGO (context only).
- "The youth exchange / training course will take place between <start> and
  <end> in <City>, <Country>." — dates and host location.
- "Working language:", "Number of participants:" — feed `description`.
- "Ideal Participant profile:" — age range and often a REQUIRED NATIONALITY
  ("Czech Nationality", "Slovak Nationality").
- "Travel cost limits:", "Insurance:" — practical info, feeds `description`.
- "HOW TO APPLY?" — application channel (email CV + motivation letter, or a
  form). Context only.

Fields:
- `name`: short English title of the project. Strip leading format tags like
  "YE", "TC", "APV+YE" if present; keep the rest as-is.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens), from the "will take place ... in <City>, <Country>"
  sentence (e.g. "in Salevere, Estonia" -> EE).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). Dates are written
  like "27.7.2026", "2.-10.6.2026" or "between 27.7.2026 and 4.8.2026"
  (day.month.year). If the month or year is missing on the start side, copy
  it from the end side. Use the ACTIVITY dates, not the application deadline.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list or group-leaders table).
    * SECONDARY SOURCE — the page text: countries named as involved in the
      project, and any REQUIRED NATIONALITY from the participant profile
      (e.g. "Czech Nationality" -> include CZ).
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder or
  bloc codes such as "XX", "EU", "EUR", or "INT". If the page only says
  "programme countries" without naming any, return null rather than
  inventing codes. Return null only when neither source names any specific
  other country.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, host location, and anything practical (working language,
  costs covered, participant profile, how to apply). Use the page's own
  facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _slug_from_href(href: str) -> str | None:
    """Return the opportunity slug from a /prilezitosti/<slug>/ href, or None."""
    if not href:
        return None
    parsed = urlparse(href)
    m = _OPPORTUNITY_PATH_RE.match(parsed.path or "")
    return m.group(1) if m else None


def _http_get(url: str) -> str | None:
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logging.warning("yysk: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "yysk: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    return response.text


def _discover(listing_html: str) -> list[tuple[str, date | None]]:
    """Extract (slug, application_deadline) pairs from the listing cards,
    preserving page order (current calls first, then the past section).

    Each card is one <a href="/prilezitosti/<slug>/"> wrapping the poster
    image and an <h4>Termín prihlášky: DD/MM/YYYY</h4>. A card whose deadline
    doesn't parse yields None — the caller extracts it and lets the
    period_end backstop decide.
    """
    soup = BeautifulSoup(listing_html, "html.parser")
    seen: set[str] = set()
    ordered: list[tuple[str, date | None]] = []
    for anchor in soup.find_all("a", href=True):
        slug = _slug_from_href(anchor["href"])
        if not slug or slug in seen:
            continue
        seen.add(slug)
        deadline: date | None = None
        m = _DEADLINE_RE.search(anchor.get_text(" ", strip=True))
        if m:
            day, month, year = (int(g) for g in m.groups())
            try:
                deadline = date(year, month, day)
            except ValueError:
                deadline = None  # malformed date on the card — extract anyway
        ordered.append((slug, deadline))
    return ordered


def _body_text(detail_html: str) -> str:
    """Strip nav/footer/script noise from a detail page and return readable
    text. The Customify theme wraps the call in <main>; decomposing header/
    nav/footer keeps the site-wide menu out of the LLM input."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup
    return main.get_text("\n", strip=True)


def _info_pack_url(detail_html: str) -> str | None:
    """Find the call-specific info-pack PDF hosted on youthfullyyours.sk.

    Info-packs are optional (some calls only have a poster image) and are
    linked from the post body, typically anchored "HERE". Site-wide PDFs (the
    HILL Manifesto lives in the desktop AND mobile nav menus) are excluded
    structurally — any anchor inside nav/header/footer or menu-item markup is
    ignored, so future site-wide PDFs stay excluded without a blocklist.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    for tag in soup(["nav", "footer", "header"]):
        tag.decompose()
    for anchor in soup.find_all("a", href=True):
        if anchor.find_parent(class_=re.compile(r"menu-item")):
            # WP menu markup outside <nav> (e.g. a sidebar/mobile menu). NB:
            # match "menu-item", not "menu" — <body> carries a
            # "menu_sidebar_slide_left" class that would swallow everything.
            continue
        href = anchor["href"].strip()
        if not href:
            continue
        url = urljoin(_BASE, href)
        host = urlparse(url).hostname or ""
        if host not in ("youthfullyyours.sk", "www.youthfullyyours.sk"):
            continue  # Canva, Google Forms, partner sites — skip
        if url.lower().split("?")[0].endswith(".pdf"):
            return url
    return None


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    listing = _http_get(_LISTING_URL)
    if not listing:
        logging.info("yysk: listing fetch failed")
        return []

    discovered = _discover(listing)
    if not discovered:
        logging.info("yysk: listing returned 0 opportunity cards")
        return []

    candidates = {f"{_ID_PREFIX}{slug}": (slug, deadline)
                  for slug, deadline in discovered}
    seen = seen_ids(candidates.keys())
    fresh = {eid: v for eid, v in candidates.items() if eid not in seen}
    logging.info(
        "yysk: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, (slug, deadline) in fresh.items():
        # Deadline pre-filter: the listing keeps every past call forever, so
        # this (not the period_end backstop) is what keeps the historical
        # backlog away from the detail fetch + LLM. Non-retryable — a passed
        # deadline never un-passes.
        if deadline is not None and deadline < today:
            logging.info(
                "yysk: skipping %s (deadline passed on %s)",
                event_id, deadline.isoformat(),
            )
            mark_skipped(event_id, ADAPTER_NAME, "deadline_passed")
            continue

        detail_url = f"{_BASE}/prilezitosti/{slug}/"
        detail_html = _http_get(detail_url)
        if not detail_html:
            continue  # transient — retry next cycle

        body = _body_text(detail_html)
        if not body.strip():
            logging.info("yysk: skipping %s (empty body)", event_id)
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(detail_html)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "yysk: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "yysk: no self-hosted info-pack PDF for %s (poster-only call)",
                event_id,
            )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "yysk: skipping %s (format=%s, name=%r)",
                event_id, fmt, extracted["name"],
            )
            mark_skipped(event_id, ADAPTER_NAME, f"format_{fmt}")
            continue

        try:
            end = datetime.strptime(extracted["period_end"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if end < today:
            logging.info(
                "yysk: skipping %s (already ended on %s)",
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
            "url": detail_url,
            "raw": {
                "slug": slug,
                "application_deadline": (
                    deadline.isoformat() if deadline else None
                ),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("yysk: returning %d items", len(items))
    return items
