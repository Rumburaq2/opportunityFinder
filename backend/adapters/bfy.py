"""Brno for you (brnoforyou.cz) adapter: ingests Czech-language NGO project
posts from the "Aktuální nabídky projektů" listing filtered to youth-exchange
projects.

Unlike EYC, the site doesn't expose a project-specific RSS feed (the site-wide
/feed/ is past-participant blog posts, not open calls). Flow per hourly cycle:

  1. GET the listing page with the server-side ?cat=ye filter; parse anchors
     to `/projekty/<slug>/` to discover candidate projects.
  2. Compute candidate ids `bfy:<slug>`; ask events_writer which already exist
     and drop those (no LLM call for unchanged posts).
  3. For each new id, GET its detail page, extract the article body, and look
     for a project-specific info-pack PDF on the brnoforyou.cz domain. Generic
     admin/T&C PDFs and Canva links are skipped — see _info_pack_url.
  4. Pass the body (and PDF when present) to llm_extractor with the bespoke
     Czech prompt. Drop items where `is_youth_exchange=false` or
     `period_end < today`.

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

SOURCE = "youth_exchange"
ADAPTER_NAME = "bfy"

_LISTING_URL = "https://www.brnoforyou.cz/aktualni-nabidky-projektu/?cat=ye"
_BASE = "https://www.brnoforyou.cz"
_ID_PREFIX = "bfy:"

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Project URLs look like /projekty/<slug>/ . Trailing slash is optional; we
# normalize it. Multi-segment slugs aren't used by the site.
_PROJECT_PATH_RE = re.compile(r"^/projekty/([a-z0-9][a-z0-9-]*)/?$")

# The site reuses these generic PDFs across every project — they're not the
# event's info-pack and shouldn't be sent to Gemini.
_GENERIC_PDF_HINTS = (
    "administrativni-cast",
    "vseobecne-podminky",
)


EXTRACTION_PROMPT = """\
You extract a single Youth Exchange event from a Czech-language NGO project
page (Brno for you, https://www.brnoforyou.cz). The page uses a structured
field-style layout, not free prose. An info-pack PDF may be attached to this
request — when present, prefer the PDF for `partner_countries`, `country`
(host), and exact dates. When no PDF is attached, extract from the page text
alone; do NOT make up data.

Definitions:
- A Youth Exchange (Czech: "Mládežnická výměna" / "Výměna mládeže") is
  specifically an Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30. The page is filtered to this category, so almost
  every post should qualify; set `is_youth_exchange` to FALSE only for
  obviously different formats (Training Course / "Tréninkový kurz",
  European Solidarity Corps / ESC / "Evropský sbor solidarity", Strategic
  Seminar, Mobility of Youth Workers, Job Shadowing, conference).

The page typically uses these Czech field labels — look for them first:
- "Termín" — date range of the activity.
- "Lokalita" — host city and country. If two locations are listed (rare
  binational exchanges), pick the first one as the host.
- "Zaměření" / "Téma projektu" / "Popis projektu" — feeds `description`.
- "Profil účastníků" — age range and participant counts.
- "Zapojené země" / "Účastnické země" / "Účastníci" — list of participating
  countries. INCLUDES the host; you must EXCLUDE the host from
  `partner_countries`.
- "Praktické informace" / "Přihlašování do" — application deadline (not
  needed in output, but useful context).

Fields:
- `name`: short English title of the project. The page title is usually
  already English; keep it as-is but strip "MV –" / "Výměna mládeže –"
  prefixes if present.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens). Czech country names → Itálie=IT, Španělsko=ES,
  Německo=DE, Francie=FR, Polsko=PL, Slovensko=SK, Maďarsko=HU, Rumunsko=RO,
  Bulharsko=BG, Řecko=GR, Portugalsko=PT, Chorvatsko=HR, Slovinsko=SI,
  Litva=LT, Lotyšsko=LV, Estonsko=EE, Finsko=FI, Švédsko=SE, Dánsko=DK,
  Nizozemsko/Holandsko=NL, Belgie=BE, Rakousko=AT, Irsko=IE, Malta=MT,
  Kypr=CY, Česko/Česká republika/ČR=CZ, Turecko=TR, Severní Makedonie=MK,
  Velká Británie=GB, Norsko=NO, Island=IS.
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). The "Termín" field
  uses formats like "20. 07. - 27. 07. 2026", "22 - 29. 08. 2026", or
  "6. - 12. 7. 2026". If the year is missing on one side, copy it from the
  other.
- `partner_countries`: ISO-3166 alpha-2 codes of OTHER participating
  countries (host EXCLUDED). The page almost always lists these explicitly
  under "Zapojené země" or "Účastnické země". Return null only if neither
  the page text nor the PDF mentions any partners.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, location, and anything practical (cost, working language,
  what participants will do). Use the page's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _slug_from_href(href: str) -> str | None:
    """Return the project slug from a /projekty/<slug>/ href, or None."""
    if not href:
        return None
    parsed = urlparse(href)
    path = parsed.path or ""
    m = _PROJECT_PATH_RE.match(path)
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
        logging.warning("bfy: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "bfy: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    return response.text


def _discover_slugs(listing_html: str) -> list[str]:
    """Extract the unique set of project slugs visible on the listing page,
    preserving the order they appear."""
    soup = BeautifulSoup(listing_html, "html.parser")
    seen: set[str] = set()
    ordered: list[str] = []
    for anchor in soup.find_all("a", href=True):
        slug = _slug_from_href(anchor["href"])
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def _body_text(detail_html: str) -> str:
    """Strip nav/footer/script noise from a detail page and return readable
    text. Gemini handles the resulting layout fine even without explicit
    field separators."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup
    return main.get_text("\n", strip=True)


def _info_pack_url(detail_html: str, slug: str) -> str | None:
    """Find the project-specific info-pack PDF on brnoforyou.cz, ignoring
    Canva designs (can't fetch, can't render) and the site's generic admin /
    T&C PDFs that appear on every project page.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href:
            continue
        url = urljoin(_BASE, href)
        host = urlparse(url).hostname or ""
        if host != "www.brnoforyou.cz" and host != "brnoforyou.cz":
            continue  # Canva, MS Forms, other partner sites — skip
        if not url.lower().endswith(".pdf"):
            continue
        lower = url.lower()
        if any(hint in lower for hint in _GENERIC_PDF_HINTS):
            continue
        anchor_text = (anchor.get_text() or "").strip().lower()
        if "infopack" in lower or "info-pack" in lower or "infopack" in anchor_text:
            return url
        # Fallback: a self-hosted PDF whose filename contains the slug is
        # almost certainly the info-pack for this project.
        if slug.replace("-", "").lower() in lower.replace("-", ""):
            return url
    return None


def fetch() -> list[dict]:
    listing = _http_get(_LISTING_URL)
    if not listing:
        return []

    slugs = _discover_slugs(listing)
    if not slugs:
        logging.info("bfy: listing returned 0 project slugs")
        return []

    candidates = {f"{_ID_PREFIX}{slug}": slug for slug in slugs}
    seen = seen_ids(candidates.keys())
    fresh = {eid: slug for eid, slug in candidates.items() if eid not in seen}
    logging.info(
        "bfy: %d listed, %d already seen, %d to extract",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[dict] = []
    for event_id, slug in fresh.items():
        detail_url = f"{_BASE}/projekty/{slug}/"
        detail_html = _http_get(detail_url)
        if not detail_html:
            continue  # transient — retry next cycle

        body = _body_text(detail_html)
        if not body.strip():
            logging.info("bfy: skipping %s (empty body)", event_id)
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(detail_html, slug)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "bfy: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "bfy: no self-hosted info-pack PDF for %s (Canva or absent)",
                event_id,
            )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue

        if not extracted["is_youth_exchange"]:
            logging.info(
                "bfy: skipping %s (not a Youth Exchange: %r)",
                event_id, extracted["name"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "not_youth_exchange")
            continue

        try:
            end = datetime.strptime(extracted["period_end"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if end < today:
            logging.info(
                "bfy: skipping %s (already ended on %s)",
                event_id, extracted["period_end"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        items.append({
            "id": event_id,
            "name": extracted["name"],
            "description": extracted["description"],
            "period_start": extracted["period_start"],
            "period_end": extracted["period_end"],
            "country": extracted["country"],
            "partner_countries": extracted["partner_countries"],
            "url": detail_url,
            "raw": {
                "slug": slug,
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        })

    logging.info("bfy: returning %d Youth Exchange items", len(items))
    return items
