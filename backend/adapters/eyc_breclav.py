"""EYC Břeclav adapter: ingests Czech-language NGO posts from the "Zahraniční
projekty" RSS feed at eycb.eu.

Flow per hourly cycle:
  1. Fetch the RSS feed via feedparser.
  2. Compute candidate ids `eyc:<entry.id>`; ask events_writer which already
     exist and drop those (so we don't burn Gemini quota on re-runs).
  3. For each new entry, call llm_extractor with the bespoke Czech prompt and
     the entry's full HTML body.
  4. Drop items where `is_youth_exchange=false` (Training Courses, ESC, etc.)
     or `period_end < today` (no point notifying about events that already
     ended).
  5. Return the remaining items in the shape upsert_events expects for
     source='youth_exchange'.

State lives in the `events` table itself — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

import feedparser

from events_writer import mark_skipped, seen_ids
from llm_extractor import extract
from pdf_fetcher import fetch_pdf

SOURCE = "youth_exchange"
ADAPTER_NAME = "eyc_breclav"

_FEED_URL = "https://eycb.eu/category/zahranicni-projekty/feed/"
_ID_PREFIX = "eyc:"


EXTRACTION_PROMPT = """\
You extract a single Youth Exchange event from a Czech-language NGO blog post
(EYC Břeclav, https://eycb.eu). An English-language info-pack PDF is often
attached to this request — it is the authoritative source. When the PDF is
present, prefer its data for `partner_countries`, `country` (host), and the
event dates; the Czech post body is usually shorter and sometimes omits the
partner list entirely. Return JSON matching the provided schema.

Definitions:
- A Youth Exchange (Czech: "Mládežnická výměna") is specifically an Erasmus+
  Key Action 1 youth-mobility activity for participants aged ~13–30. The post
  usually flags this with phrases like "Klíčová akce 1", "KA1", "Mládežnická
  výměna", or "Youth Exchange".
- Set `is_youth_exchange` to FALSE for every other format, including:
  Training Course / "Tréninkový kurz" / "Školení mládežnických pracovníků",
  European Solidarity Corps / ESC / "Evropský sbor solidarity" / "dlouhodobá
  dobrovolnická služba", Strategic Seminar / "Strategický seminář",
  Mobility of Youth Workers, Job Shadowing, Study Visit, conference.

Fields:
- `name`: short English title of the project. If the post only gives a Czech
  title, translate it. Strip prefixes like "MV –" / "Mládežnická výměna –".
- `country`: ISO-3166 alpha-2 of the HOST country (where the event physically
  happens). The post usually states this as "Místo konání:", "Hostitelská
  země:", or "Země realizace:". Examples of Czech country names →
  Itálie=IT, Španělsko=ES, Německo=DE, Francie=FR, Polsko=PL, Slovensko=SK,
  Maďarsko=HU, Rumunsko=RO, Bulharsko=BG, Řecko=GR, Portugalsko=PT,
  Chorvatsko=HR, Slovinsko=SI, Litva=LT, Lotyšsko=LV, Estonsko=EE, Finsko=FI,
  Švédsko=SE, Dánsko=DK, Nizozemsko/Holandsko=NL, Belgie=BE, Rakousko=AT,
  Irsko=IE, Malta=MT, Kypr=CY, Česko/Česká republika/ČR=CZ, Turecko=TR,
  Severní Makedonie=MK, Velká Británie=GB, Norsko=NO, Island=IS.
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD). The post usually
  states this as "Termín konání:" or "Termín:". Czech date formats include
  "21.5.–30.5.2026", "21.–30. května 2026", or "5/2026". If only a month is
  given, use the 1st and last day of that month. If the year is missing,
  infer from context.
- `partner_countries`: ISO-3166 alpha-2 codes of OTHER participating
  countries (excluding the host).

  PRIMARY SOURCE — the info-pack PDF if one is attached. Erasmus+
  Youth Exchange info packs ALWAYS list participating countries. Read
  the whole PDF and look in any of these places:
    * a "Participating organisations" / "Partner organisations" /
      "Project partners" section listing one organisation per country;
    * a "Countries involved" / "Participating countries" list;
    * a "Group leaders" table with one row per national group;
    * a flag/country row in the project-overview section;
    * the budget/reimbursement table (it normally has one row per
      sending country).
  Extract every country that appears in any of these and exclude the
  host country itself. Do not invent countries that are not mentioned,
  but do not return null just because the partner list isn't in the
  first paragraph — it is almost always somewhere in the PDF.

  SECONDARY SOURCE — the Czech post body, under "Země:", "Účastníci:",
  "Partnerské země:", or similar. Use this only if no PDF is attached
  or the PDF genuinely doesn't list partners.

  Return null ONLY when neither source mentions any partner countries.
- `description`: 80–160 word English summary of the project (topic, target
  group, dates, location, anything practical like cost or working language).
  Use the post's own facts; do not embellish.

If any required field is missing or ambiguous, still return your best guess —
post-validation will drop obviously broken extractions.
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


_INFO_PACK_RE = re.compile(
    r"https?://drive\.google\.com/file/d/[A-Za-z0-9_-]+(?:/[^\s\"<>]*)?",
)


def _info_pack_url(body: str) -> str | None:
    # EYC posts consistently link the info pack as the only Google Drive URL
    # in the body. Take the first match.
    m = _INFO_PACK_RE.search(body)
    return m.group(0) if m else None


def fetch() -> list[dict]:
    parsed = feedparser.parse(_FEED_URL)
    if parsed.bozo:
        logging.warning(
            "eyc_breclav: feed parse reported bozo=%s (%s)",
            parsed.bozo, getattr(parsed, "bozo_exception", "?"),
        )

    entries = parsed.entries or []
    if not entries:
        logging.info("eyc_breclav: feed returned 0 entries")
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
        "eyc_breclav: %d entries, %d already seen, %d to extract",
        len(by_id), len(seen), len(fresh),
    )

    today = date.today()
    items: list[dict] = []
    for event_id, entry in fresh.items():
        body = _entry_body(entry)
        if not body.strip():
            logging.info("eyc_breclav: skipping %s (empty body)", event_id)
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(body)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "eyc_breclav: info-pack fetch failed for %s, falling back "
                    "to text-only extraction (url=%s)",
                    event_id, info_pack,
                )

        extracted = extract(EXTRACTION_PROMPT, body, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        if not extracted["is_youth_exchange"]:
            logging.info(
                "eyc_breclav: skipping %s (not a Youth Exchange: %r)",
                event_id, extracted["name"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "not_youth_exchange")
            continue

        try:
            end = datetime.strptime(extracted["period_end"], "%Y-%m-%d").date()
        except ValueError:
            continue  # transient parse failure — retry next cycle
        if end < today:
            logging.info(
                "eyc_breclav: skipping %s (already ended on %s)",
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
            "url": entry.get("link"),
            "raw": {
                "rss_id": event_id[len(_ID_PREFIX):],
                "rss_title": entry.get("title"),
                "rss_published": entry.get("published"),
                "llm": extracted,
            },
        })

    logging.info("eyc_breclav: returning %d Youth Exchange items", len(items))
    return items
