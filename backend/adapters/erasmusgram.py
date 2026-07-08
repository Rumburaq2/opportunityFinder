"""Erasmusgram (erasmusgram.com) adapter: first Turkish source. Ingests youth
exchanges from the curated "Gençlik Değişimi Projeleri" category via the
WordPress core REST API.

Discovery follows the YIC lesson — probe `wp-json` before scraping.
erasmusgram.com is WordPress but exposes no Tribe Events API (404); the core
API works and beats scraping the rendered category page:

    GET /wp-json/wp/v2/posts?categories=2647&per_page=50

  - category 2647 = "Gençlik Değişimi Projeleri" (Youth Exchange Projects),
    one of several activity-type buckets the site maintains (training courses
    = 2648, short/long ESC = 2645/2646, seminars = 2663). The separation is
    clean, so — ADEL-style — the category reliably means "youth exchange"; the
    LLM's classification is kept only as a guard that drops the rare
    online-only / mis-posted item (format 'other');
  - numeric post ids → dedup id `erasmusgram:<id>`;
  - full post body in `content.rendered` — one HTTP request per cycle, no
    detail-page fetches.

Erasmusgram is an aggregator that reposts other NGOs' Erasmus+ calls for a
Turkish audience, so the sending country is TR (folded into every event's
eligibility set per the Phase 4f-B national-adapter regime). Posts are rich
Turkish prose with emoji-labelled fields: the HOST country + city sit in both
the title and an "Ev Sahibi Şehir" line; the activity dates in a "Proje
Tarihleri" line (distinct from "Seyahat Tarihleri" = travel days). Participating
countries are usually NOT named, so `partner_countries` is often null — a
correct, expected answer.

Info-packs are Google Drive file links (the "bilgi paketi"), present on
roughly a quarter of posts; pdf_fetcher rewrites the Drive share URL. The
near-ubiquitous Google Forms / forms.gle links are APPLICATION forms, not
info-packs, and are deliberately not matched.

There are no machine date fields (YIC-style), so the LLM extracts the activity
dates from the prose; the feed reaches back months, so the first run marks the
ended backlog via the period_end backstop.

robots.txt only disallows /wp-admin/ and /cgi-bin/ — wp-json is fully open.

Returns (source, item) pairs so the caller can route each item to the right
events.source bucket.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import html
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

ADAPTER_NAME = "erasmusgram"

# Turkish aggregator source — TR folded into every event's eligibility set
# (Phase 4f-B national-adapter regime); see eyc_breclav for the rationale.
SENDING_COUNTRY = "TR"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

# Category 2647 = "Gençlik Değişimi Projeleri" (Youth Exchange Projects).
_API_URL = (
    "https://www.erasmusgram.com/wp-json/wp/v2/posts?categories=2647&per_page=50"
)
_ID_PREFIX = "erasmusgram:"

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Info-pack = a Google Drive FILE link in the post body (the "bilgi paketi").
# Application forms (docs.google.com/forms, forms.gle) are near-ubiquitous and
# deliberately NOT matched — pdf_fetcher would reject the HTML anyway, but
# matching one would waste a fetch and mask a real info-pack.
_DRIVE_LINK_RE = re.compile(
    r"https://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^\"'\s<>]*"
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from a Turkish-language post by
Erasmusgram (https://www.erasmusgram.com), an aggregator that reposts youth
exchange calls for a Turkish audience. The input starts with a "Title:" line
(the post's title — it carries the HOST country, city, and duration, e.g.
"Romanya , Baia Mare: 7 Gün Gençlik Değişimi") followed by the post body. The
body is rich Turkish prose with emoji-labelled structured fields. An info-pack
PDF ("bilgi paketi") is sometimes attached to this request — when present,
prefer it for `partner_countries`, `country` (host), and exact dates; the
participating countries usually appear as a list or a group-leaders/budget
table, and practical details (travel reimbursement, age limits) near the end.
When no PDF is attached, extract from the post text alone; do NOT invent data.

Classify the post into one of three formats via the `format` field:
- "youth_exchange" — Erasmus+ Key Action 1 youth-mobility activity for
  participants aged ~13–30 ("Gençlik Değişimi", "youth exchange"). This is by
  far the most common case for this source.
- "training_course" — Erasmus+ Key Action 1 training for youth workers /
  leaders / trainers ("eğitim kursu", "training course", "TC"). Target group
  is youth workers, NOT teen participants.
- "other" — everything else: European Solidarity Corps / ESC volunteering
  ("gönüllülük"), study visits, job shadowing, seminars, conferences; and any
  ONLINE-ONLY offering (webinars, e-learning, online courses) — no meaningful
  host country, not a KA1 mobility. The extraction will be discarded.

Fields:
- `name`: short English title of the PROJECT — its proper name (e.g.
  "Sustainable Connections", "GREEN DREAM"), usually given in English inside
  the body. Drop the Turkish recruiting phrases, the host city, and emoji
  around it. Do NOT use the Turkish "<Country>, <City>: N Gün Gençlik
  Değişimi" headline as the name if a real project title is present.
- `country`: ISO-3166 alpha-2 of the HOST country (where the activity
  physically happens), from the title and the "Ev Sahibi Şehir" (host city)
  line. Turkish country names → Almanya=DE, Fransa=FR, İtalya=IT, İspanya=ES,
  Portekiz=PT, Polonya=PL, Romanya=RO, Yunanistan=GR, Hırvatistan=HR,
  Macaristan=HU, Çekya/Çek Cumhuriyeti=CZ, Slovakya=SK, Slovenya=SI,
  Litvanya=LT, Letonya=LV, Estonya=EE, Bulgaristan=BG, Avusturya=AT,
  Hollanda=NL, Belçika=BE, Lüksemburg=LU, İrlanda=IE, Finlandiya=FI,
  İsveç=SE, Danimarka=DK, Norveç=NO, İzlanda=IS, Malta=MT,
  Kıbrıs/Güney Kıbrıs=CY, Türkiye=TR, Kuzey Makedonya=MK, Sırbistan=RS,
  Karadağ=ME, Arnavutluk=AL, Bosna Hersek=BA, Gürcistan=GE, Ukrayna=UA,
  İngiltere/Birleşik Krallık=GB. A Turkish city with no country named (e.g.
  "İstanbul", "Silivri") means the host is Türkiye -> TR.
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD) of the ACTIVITY itself,
  from the "Proje Tarihleri" line (e.g. "10 – 16 Ağustos 2026"). IMPORTANT:
  use "Proje Tarihleri" (project/activity dates), NOT "Seyahat Tarihleri"
  (travel days, which include the arrival/departure buffer) and NOT the
  application deadline ("Son Başvuru"). Turkish month names: Ocak=01,
  Şubat=02, Mart=03, Nisan=04, Mayıs=05, Haziran=06, Temmuz=07, Ağustos=08,
  Eylül=09, Ekim=10, Kasım=11, Aralık=12. If the year is missing on the start
  side, copy it from the end side. If NEITHER the body nor an attached PDF
  states the activity dates, do NOT invent a plausible-looking range — set
  `format` to "other" instead (an event whose dates cannot be stated is
  unusable and must be discarded).
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list, group-leaders table, or budget/reimbursement table with one row
      per sending country).
    * SECONDARY SOURCE — the post prose, where countries are actually NAMED.
      A participant count alone ("5 katılımcı") is NOT a country list, and
      vague phrases like "farklı kültürlerden katılımcılar" (participants from
      different cultures) or "farklı ülkelerden gençler" (young people from
      different countries) name NOBODY.
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder or
  bloc codes such as "XX", "EU", "EUR", or "INT". The Turkish→ISO mapping in
  the `country` field above is a TRANSLATION AID for names that actually appear
  in the sources — it is NOT a list of candidates to copy from. If neither the
  PDF nor the text NAMES specific countries, `partner_countries` MUST be null —
  a null here is a correct, expected answer, not a failure.
- `description`: 80–160 word English summary covering the topic, target group,
  dates, host location, and anything practical (working language, participant
  age range, costs covered, number of Turkish places, how to apply). Use the
  post's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best guess —
post-validation will drop obviously broken extractions.
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
        logging.warning("erasmusgram: GET failed url=%s err=%s", _API_URL, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "erasmusgram: non-200 url=%s status=%d",
            _API_URL, response.status_code,
        )
        return None
    try:
        posts = response.json()
    except ValueError as exc:
        logging.warning("erasmusgram: bad JSON from API err=%s", exc)
        return None
    return posts if isinstance(posts, list) else None


def _info_pack_url(body_html: str) -> str | None:
    """First Google Drive file link in the post body — the info-pack ("bilgi
    paketi"). Google Forms / forms.gle application links are not matched. None
    → text-only extraction."""
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
        logging.info("erasmusgram: API returned 0 posts")
        return []

    candidates = {f"{_ID_PREFIX}{p['id']}": p for p in posts if p.get("id")}
    seen = seen_ids(candidates.keys())
    fresh = {eid: p for eid, p in candidates.items() if eid not in seen}
    logging.info(
        "erasmusgram: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, post in fresh.items():
        body = (post.get("content") or {}).get("rendered") or ""
        if not body.strip():
            logging.info("erasmusgram: skipping %s (empty body)", event_id)
            continue

        # The host country and duration live in the title ("Romanya , Baia
        # Mare: 7 Gün Gençlik Değişimi"); prepend it so the LLM has the host
        # even when the body's "Ev Sahibi Şehir" line is phrased loosely.
        title = html.unescape(
            (post.get("title") or {}).get("rendered") or ""
        ).strip()
        content = f"Title: {title}\n\n{body}" if title else body

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(body)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "erasmusgram: info-pack fetch failed for %s, falling back "
                    "to text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info("erasmusgram: no info-pack link for %s", event_id)

        extracted = extract(EXTRACTION_PROMPT, content, pdf_bytes=pdf_bytes)
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "erasmusgram: skipping %s (format=%s, name=%r)",
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
                "erasmusgram: skipping %s (already ended on %s)",
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
                "erasmusgram_id": event_id[len(_ID_PREFIX):],
                "wp_title": (post.get("title") or {}).get("rendered"),
                "wp_date": post.get("date"),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("erasmusgram: returning %d items", len(items))
    return items
