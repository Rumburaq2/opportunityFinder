"""Európsky Dialóg (europskydialog.eu) adapter: third Slovak NGO. Ingests
open calls from the "Školenia" category RSS feed.

The EYC pattern with ADEL's Drive info-pack handling:

  - Discovery via the category feed at /category/sk/skolenia/feed/ —
    WordPress with full post bodies in `content:encoded`, so one HTTP
    request per cycle and no detail-page fetches.
  - Stable WP guids (`https://europskydialog.eu/?p=<id>`) → dedup id
    `europskydialog:<guid>`.
  - Most posts embed a Google Drive info-pack link in the body (ADEL-style);
    pdf_fetcher's Drive rewrite fetches it. docs.google.com/forms links are
    application forms, not info-packs — the Drive-only regex ignores them.
  - The category is NOT a reliable classifier: "Školenia" also carries youth
    exchanges ("Hľadáme účastníkov na mládežnícku výmenu..."), study visits,
    and free online courses. The LLM classifies; `other` (incl. online-only
    offerings — no meaningful host country) is dropped.
  - Feed reaches months back, so the first run skips a few already-ended
    items via the period_end backstop; steady state is ~1-2 posts/month.

robots.txt is fully open (empty Disallow, no content signals).

Returns (source, item) pairs so the caller can route each item to the right
events.source bucket.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

import feedparser

from events_writer import eligible_countries_for, mark_skipped, seen_ids
from llm_extractor import (
    FORMAT_TRAINING_COURSE,
    FORMAT_YOUTH_EXCHANGE,
    extract,
)
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "europskydialog"

# Slovak NGO source — SK folded into every event's eligibility set (Phase
# 4f-B national-adapter regime); see eyc_breclav for the rationale.
SENDING_COUNTRY = "SK"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

_FEED_URL = "https://europskydialog.eu/category/sk/skolenia/feed/"
_ID_PREFIX = "europskydialog:"

# Info-packs are Drive share links in the post body. Application forms are
# docs.google.com/forms — deliberately not matched.
_INFO_PACK_RE = re.compile(
    r"https?://drive\.google\.com/file/d/[A-Za-z0-9_-]+(?:/[^\s\"<>]*)?",
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from a Slovak-language open-call
post by the NGO Európsky Dialóg (https://europskydialog.eu). The post is
recruiting Slovak participants: rich prose naming the event dates, venue,
participant profile, and programme. An info-pack PDF is often attached to
this request — when present, prefer it for `partner_countries`, `country`
(host), and exact dates; the participating countries usually appear as a
list or a group-leaders/budget table, and practical details near the end.
When no PDF is attached, extract from the post text alone; do NOT invent
data.

Classify the post into one of three formats via the `format` field:
- "youth_exchange" — Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30 ("mládežnícka výmena", "youth exchange").
- "training_course" — Erasmus+ Key Action 1 training for youth workers /
  leaders / trainers ("školenie pre pracovníkov s mládežou", "tréning",
  "training course", "TC"). Target group is youth workers, NOT teen
  participants.
- "other" — everything else: study visits ("študijná návšteva"), European
  Solidarity Corps / ESC volunteering, job shadowing, seminars,
  conferences, surveys, discussions — AND any offering that is
  ONLINE-ONLY (e.g. "bezplatný online kurz", webinars, e-learning). An
  online course has no meaningful host country and is not a KA1 mobility;
  classify it "other" even if the post calls it a training. The extraction
  will be discarded.

The category tag is NOT reliable — youth exchanges and study visits get
posted under "Školenia" too. Classify from the post's own description of
the activity and target group.

Fields:
- `name`: short English title of the project. Posts title like "Hľadáme
  účastníkov na Erasmus+ mládežnícku výmenu Scroll Smart!" or
  "GreenSpirED: Tréning v Taliansku pre mladých..." — extract the proper
  project name ("Scroll Smart", "GreenSpirED"), dropping the Slovak
  recruiting phrases around it.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens). Slovak country names → Taliansko=IT, Španielsko=ES,
  Nemecko=DE, Francúzsko=FR, Poľsko=PL, Česko/Česká republika=CZ,
  Maďarsko=HU, Rumunsko=RO, Bulharsko=BG, Grécko=GR, Portugalsko=PT,
  Chorvátsko=HR, Slovinsko=SI, Litva=LT, Lotyšsko=LV, Estónsko=EE,
  Fínsko=FI, Švédsko=SE, Dánsko=DK, Holandsko=NL, Belgicko=BE, Rakúsko=AT,
  Írsko=IE, Malta=MT, Cyprus=CY, Slovensko=SK, Turecko=TR, Severné
  Macedónsko=MK, Nórsko=NO, Island=IS. Many of this NGO's events are
  hosted in Slovakia itself (e.g. "v Suchej nad Parnou pri Trnave" -> SK).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). Prose dates like
  "od 29. júna do 6. júla 2026" (Slovak month names: januára=01,
  februára=02, marca=03, apríla=04, mája=05, júna=06, júla=07, augusta=08,
  septembra=09, októbra=10, novembra=11, decembra=12). If the year is
  missing on the start side, copy it from the end side. Use the ACTIVITY
  dates, not the application deadline.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list, group-leaders table, or budget/reimbursement table with one row
      per sending country).
    * SECONDARY SOURCE — the post prose, which often counts or names the
      countries ("z piatich krajín Európy" alone is NOT a list — extract
      only countries actually NAMED).
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder
  or bloc codes such as "XX", "EU", "EUR", or "INT". If the sources only
  count countries or say "programme countries" without naming them, return
  null rather than inventing codes.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, host location, and anything practical (working language,
  participant age range, costs covered, how to apply). Use the post's own
  facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _entry_body(entry: dict) -> str:
    content = entry.get("content")
    if isinstance(content, list) and content:
        value = content[0].get("value")
        if value:
            return value
    return entry.get("description") or entry.get("summary") or ""


def _stable_id(entry: dict) -> str | None:
    raw = entry.get("id") or entry.get("guid") or entry.get("link")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _info_pack_url(body: str) -> str | None:
    """First Drive share link in the body — the info-pack. None when the
    post has none (text-only extraction)."""
    m = _INFO_PACK_RE.search(body)
    return m.group(0) if m else None


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    parsed = feedparser.parse(_FEED_URL)
    if parsed.bozo:
        logging.warning(
            "europskydialog: feed parse reported bozo=%s (%s)",
            parsed.bozo, getattr(parsed, "bozo_exception", "?"),
        )

    entries = parsed.entries or []
    if not entries:
        logging.info("europskydialog: feed returned 0 entries")
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
        "europskydialog: %d entries, %d already seen, %d to extract",
        len(by_id), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, entry in fresh.items():
        body = _entry_body(entry)
        if not body.strip():
            logging.info("europskydialog: skipping %s (empty body)", event_id)
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(body)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "europskydialog: info-pack fetch failed for %s, falling "
                    "back to text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info(
                "europskydialog: no Drive info-pack link for %s", event_id,
            )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "europskydialog: skipping %s (format=%s, name=%r)",
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
                "europskydialog: skipping %s (already ended on %s)",
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
            "url": entry.get("link"),
            "raw": {
                "rss_id": event_id[len(_ID_PREFIX):],
                "rss_title": entry.get("title"),
                "rss_published": entry.get("published"),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("europskydialog: returning %d items", len(items))
    return items
