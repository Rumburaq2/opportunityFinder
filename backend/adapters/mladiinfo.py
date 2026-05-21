"""Mladiinfo ČR adapter: ingests Czech-language Erasmus+ open-call posts from
the "Aktuální nabídky → Erasmus+" RSS feed at mladiinfo.cz.

The site offers two related feeds; we deliberately pull the open-calls-only
one (`/category/aktualni_nabidky/erasmus-plus/feed/`) rather than the broader
`/category/erasmus-plus/feed/` so retrospective blog posts ("how it went")
don't burn Gemini quota before being classified as `other`.

Flow per hourly cycle:
  1. Fetch the RSS feed via feedparser. Body is excerpt-only (~600 chars), so
     each candidate needs a follow-up detail-page GET for the full prose.
  2. Compute candidate ids `mladiinfo:<entry.id>`; ask events_writer which
     already exist and drop those.
  3. For each new id, GET the post link, extract the article body, and look
     for an info-pack PDF. Mladiinfo splits roughly 50/50 between self-hosted
     PDFs on `mladiinfo.cz/wp-content/uploads/...` and Google Drive shares —
     pdf_fetcher handles both. Google Docs *Forms* links are application
     forms, not info-packs, and are filtered out.
  4. Pass body + PDF to llm_extractor with the bespoke Czech prompt. Trust
     the LLM's `format` classification; drop `other` and `period_end < today`.

Returns (source, item) pairs so the caller can route each item to the right
events.source bucket.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from events_writer import mark_skipped, seen_ids
from llm_extractor import (
    FORMAT_TRAINING_COURSE,
    FORMAT_YOUTH_EXCHANGE,
    extract,
)
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "mladiinfo"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

_FEED_URL = "https://www.mladiinfo.cz/category/aktualni_nabidky/erasmus-plus/feed/"
_ID_PREFIX = "mladiinfo:"

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_DRIVE_FILE_RE = re.compile(
    r"^https?://drive\.google\.com/file/d/[A-Za-z0-9_-]+",
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from a Czech-language NGO blog
post on Mladiinfo ČR (https://www.mladiinfo.cz). Posts are free-prose
Czech, often emoji-decorated, not field-style. An info-pack PDF — either
self-hosted on mladiinfo.cz or shared via Google Drive — is often attached
to this request; when present, prefer the PDF for `partner_countries`,
`country` (host), and exact dates. When no PDF is attached, extract from the
post text alone; do NOT invent data.

Classify the post into one of three formats via the `format` field:
- "youth_exchange" — Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30. Czech cues: "výměna mládeže", "mládežnická
  výměna", "Youth Exchange", "YE".
- "training_course" — Erasmus+ Key Action 1 training for youth workers /
  leaders / trainers. Czech cues: "tréninkový kurz", "školení", "Training
  Course", "TC", "Mobility of Youth Workers", "pracovníci s mládeží". The
  target group is youth workers, NOT teen participants.
- "other" — every other format: ESC / "Evropský sbor solidarity" /
  dlouhodobá dobrovolnická služba, Strategic Seminar, Job Shadowing, Study
  Visit, conference, retrospectives ("jak to bylo"). The extraction will be
  discarded.

Mladiinfo posts typically include emoji-prefixed practical info blocks:
- 🌍 / "Místo:" — host city and country.
- 📅 / "Datum:" / "Termín:" — date range.
- 🚌 — travel reimbursement (not extracted).
- 🏠 — accommodation/board (not extracted).
- "Koho hledáme?" — target group + age range.
- Inline partner-country mentions like "Pokud máš kámoše/kámošky ze
  Španělska, Severní Makedonie, Polska, Ukrajiny…" — these list the partner
  countries.

Fields:
- `name`: short English title of the project. If the Czech title contains
  an English project name (very common, e.g. "Tréninkový kurz CTRL+ALT+NATURE
  v Itálii" → "CTRL+ALT+NATURE"), extract that. Otherwise translate.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens). Czech country names → Itálie=IT, Španělsko=ES,
  Německo=DE, Francie=FR, Polsko=PL, Slovensko=SK, Maďarsko=HU, Rumunsko=RO,
  Bulharsko=BG, Řecko=GR, Portugalsko=PT, Chorvatsko=HR, Slovinsko=SI,
  Litva=LT, Lotyšsko=LV, Estonsko=EE, Finsko=FI, Švédsko=SE, Dánsko=DK,
  Nizozemsko/Holandsko=NL, Belgie=BE, Rakousko=AT, Irsko=IE, Malta=MT,
  Kypr=CY, Česko/Česká republika/ČR=CZ, Turecko=TR, Severní Makedonie=MK,
  Velká Británie=GB, Norsko=NO, Island=IS, Ukrajina=UA, Gruzie=GE,
  Moldavsko=MD, Arménie=AM, Albánie=AL, Srbsko=RS, Bosna a Hercegovina=BA,
  Černá Hora=ME.
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). The post usually
  writes dates as "12. 7. – 19. 7. 2026" or "21.5.–30.5.2026". If the year
  is missing on one side, copy it from the other.
- `partner_countries`: ISO-3166 alpha-2 codes of OTHER participating
  countries (host EXCLUDED).

  PRIMARY SOURCE — the info-pack PDF if one is attached. Erasmus+ info
  packs ALWAYS list participating countries. Look in any of these places:
    * a "Participating organisations" / "Partner organisations" /
      "Project partners" section;
    * a "Countries involved" / "Participating countries" list;
    * a "Group leaders" table with one row per national group;
    * a flag/country row in the project-overview section;
    * the budget/reimbursement table.

  SECONDARY SOURCE — the post body. Mladiinfo posts often name partners
  inline ("kámoše/kámošky ze Španělska, Severní Makedonie, Polska,
  Ukrajiny nebo Bulharska") — treat that sentence as the partner list.

  Return null ONLY when neither source mentions any partner countries.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, location, and anything practical (cost, working language,
  what participants will do). Use the post's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _stable_id(entry: dict) -> str | None:
    raw = entry.get("id") or entry.get("guid") or entry.get("link")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _clean_url(url: str) -> str:
    """Strip query/fragment from the post URL — feed links carry UTM noise
    that would store noisily and break dedup if it ever drifted."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _http_get(url: str) -> str | None:
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logging.warning("mladiinfo: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "mladiinfo: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    return response.text


def _body_text(detail_html: str) -> str:
    soup = BeautifulSoup(detail_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup
    return main.get_text("\n", strip=True)


def _slug_from_link(link: str) -> str:
    """Final path segment of /YYYY/MM/DD/<slug>/ — used as a tiebreaker when
    multiple PDFs match."""
    path = urlparse(link).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _info_pack_url(detail_html: str, slug: str) -> str | None:
    """Find the info-pack URL on a mladiinfo post.

    Accept: self-hosted .pdf on mladiinfo.cz, or any drive.google.com/file/d/
    share (pdf_fetcher rewrites those to direct-download).
    Skip: docs.google.com (application forms — never the info-pack).
    Prefer: anchors whose href or text mentions "infopack" / "info-pack", or
    whose filename contains the post slug.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    candidates: list[tuple[int, str]] = []  # (priority, url) — lower = better
    slug_compact = slug.replace("-", "").lower()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href:
            continue
        host = (urlparse(href).hostname or "").lower()
        if host in ("docs.google.com",):
            continue  # application forms / docs — not info-packs

        is_self_pdf = (
            host in ("www.mladiinfo.cz", "mladiinfo.cz")
            and href.lower().endswith(".pdf")
        )
        is_drive_file = bool(_DRIVE_FILE_RE.match(href))
        if not (is_self_pdf or is_drive_file):
            continue

        anchor_text = (anchor.get_text() or "").strip().lower()
        href_lower = href.lower()
        if "infopack" in href_lower or "info-pack" in href_lower or "infopack" in anchor_text:
            priority = 0
        elif slug_compact and slug_compact in href_lower.replace("-", ""):
            priority = 1
        else:
            priority = 2
        candidates.append((priority, href))

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    parsed = feedparser.parse(_FEED_URL)
    if parsed.bozo:
        logging.warning(
            "mladiinfo: feed parse reported bozo=%s (%s)",
            parsed.bozo, getattr(parsed, "bozo_exception", "?"),
        )

    entries = parsed.entries or []
    if not entries:
        logging.info("mladiinfo: feed returned 0 entries")
        return []

    by_id: dict[str, dict] = {}
    for entry in entries:
        raw_id = _stable_id(entry)
        if not raw_id:
            continue
        by_id.setdefault(f"{_ID_PREFIX}{raw_id}", entry)

    if not by_id:
        return []

    seen = seen_ids(by_id.keys())
    fresh = {eid: entry for eid, entry in by_id.items() if eid not in seen}
    logging.info(
        "mladiinfo: %d entries, %d already seen, %d to extract",
        len(by_id), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, entry in fresh.items():
        raw_link = entry.get("link")
        if not raw_link:
            continue
        post_url = _clean_url(raw_link)

        detail_html = _http_get(post_url)
        if not detail_html:
            continue  # transient — retry next cycle

        body = _body_text(detail_html)
        if not body.strip():
            logging.info("mladiinfo: skipping %s (empty body)", event_id)
            continue

        slug = _slug_from_link(post_url)
        info_pack = _info_pack_url(detail_html, slug)
        pdf_bytes: bytes | None = None
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "mladiinfo: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "mladiinfo: no info-pack PDF for %s (text-only extraction)",
                event_id,
            )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "mladiinfo: skipping %s (format=%s, name=%r)",
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
                "mladiinfo: skipping %s (already ended on %s)",
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
            "url": post_url,
            "raw": {
                "rss_id": event_id[len(_ID_PREFIX):],
                "rss_title": entry.get("title"),
                "rss_published": entry.get("published"),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("mladiinfo: returning %d items", len(items))
    return items
