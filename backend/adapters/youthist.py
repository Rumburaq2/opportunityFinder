"""Youth IST (youthist.net) adapter: second Turkish source. Ingests youth
exchanges and training courses that Youth Istanbul (Erasmus+ OID E10389751)
recruits Turkish participants for, from the Wix CMS collection embedded in the
site's server-rendered pages.

The site is Wix, not WordPress — Tribe API, WP core API, and RSS all 404. But
no HTML scraping is needed either: every dynamic detail page under
/apply-for-projects/ embeds the ENTIRE "News" CMS collection as JSON in a
`<script id="wix-warmup-data">` tag, with clean machine fields per record:

  - `_id` UUID → dedup id `youthist:<uuid>`;
  - `status` ("Open to Applications" / "Completed") — free pre-filter, the
    Completed backlog never reaches the LLM;
  - `deadlineForApplication` ISO date — second pre-filter (YYSK lesson: a
    passed deadline never un-passes);
  - `projectType` ("Youth Exchange" / "Training Course") — authoritative
    classification, the LLM only guards against non-open-call posts;
  - `location.country` — ISO-2 host, already machine-readable;
  - `richcontent` — the full body as a node tree: prose for the LLM plus
    `fileData` nodes carrying the info-pack PDF (id + size).

Per-cycle flow (3 HTTP requests, LLM only on fresh open calls, ~1/month):
sitemap index → dynamic apply-for-projects child sitemap (its URL embeds a
Wix hash, so it is re-discovered each cycle rather than hardcoded) → one
detail page → parse warmup JSON → pre-filter → extract fresh ids only. This
beat scraping the listing page, which is 660KB of Wix markup with no dates or
deadlines on the cards — and no warmup collection at all.

Source quirks:
  - `projectDates` is year-less ("17 - 25 Aug"); the body prose carries the
    full range ("Dates (including travel days): 17-25 August 2026") and the
    ISO deadline anchors the year — the LLM parses dates from the body.
  - Info-packs are self-hosted Wix documents at /_files/ugd/<id>.pdf. The
    matched record's own `infopack` field / `richcontent` fileData node is
    used — the surrounding page HTML contains OTHER projects' PDFs too.
    fileData carries the byte size, so oversized packs (one live pack is
    20.7MB, over pdf_fetcher's 20MB cap) skip the download and go text-only.
  - Applications go via forms.gle — never matched as info-packs.
  - Some posts are "Internal Participation" FYIs (participation internal to
    Youth IST, not an open call) — the prompt classifies these as `other`.
  - The warmup collection showed no pagination at 8 records; the adapter
    warns if the sitemap lists more URLs than the collection has records.

robots.txt: `Allow: /` for `*`; only `*?lightbox=` URLs are disallowed and
the adapter emits no query parameters. The sitemap is explicitly advertised.
wix-warmup-data is Wix's internal SSR format — if it changes, the adapter
fails soft to [] with a warning and other adapters are untouched.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import json
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

ADAPTER_NAME = "youthist"

# Turkish NGO recruiting Turkish participants — TR folded into every event's
# eligibility set (Phase 4f-B national-adapter regime).
SENDING_COUNTRY = "TR"

_FORMAT_TO_SOURCE = {
    FORMAT_YOUTH_EXCHANGE: "youth_exchange",
    FORMAT_TRAINING_COURSE: "training_course",
}

_BASE = "https://www.youthist.net"
_SITEMAP_INDEX_URL = f"{_BASE}/sitemap.xml"
_ID_PREFIX = "youthist:"
_STATUS_OPEN = "Open to Applications"

# The child sitemap listing the /apply-for-projects/ detail pages. Its
# filename embeds a Wix-generated hash that could rotate, so it is matched by
# prefix in the index instead of hardcoded.
_DYNAMIC_SITEMAP_RE = re.compile(
    r"https://www\.youthist\.net/dynamic-apply-for-projects[^<]*-sitemap\.xml"
)
_SITEMAP_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")

_WARMUP_SCRIPT_RE = re.compile(
    r'<script[^>]*id="wix-warmup-data"[^>]*>(.*?)</script>', re.S,
)

# wix:document://v1/ugd/<file>.pdf/<display-name>  →  /_files/ugd/<file>.pdf
_WIX_DOCUMENT_RE = re.compile(r"wix:document://v1/(ugd/[^/]+)")

# pdf_fetcher rejects bodies over this size; fileData nodes state the size up
# front, so oversized packs skip the download entirely (text-only fallback).
_MAX_PDF_BYTES = 20 * 1024 * 1024

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from an English-language post by
Youth IST (https://www.youthist.net), a Turkish NGO that recruits participants
from Türkiye for youth exchanges and training courses abroad. The input starts
with a structured header (title, project type, host country, application
deadline, project dates) taken from the site's own CMS — treat those header
values as authoritative. The prose after the header is the project
description. An info-pack PDF is sometimes attached to this request — when
present, prefer it for `partner_countries` and exact dates; the participating
countries usually appear as a participating-countries list, a group-leaders
table, or a travel-reimbursement/budget table with one row per sending country
(e.g. "Türkiye: 309€"), and practical details (age limits, costs covered)
near the end. When no PDF is attached, extract from the text alone; do NOT
invent data.

Classify the post into one of three formats via the `format` field:
- "youth_exchange" / "training_course": copy the header's "Project type"
  value ("Youth Exchange" -> "youth_exchange", "Training Course" ->
  "training_course") UNLESS one of the `other` conditions below applies.
- "other" — use this even when the header names a type, if the post is:
  an "Internal Participation" announcement (participation is internal to
  Youth IST, not an open call — the body says so explicitly); an ONLINE-ONLY
  offering (webinar, e-learning, online course — no meaningful host country,
  not a KA1 mobility); or any non-KA1 activity (ESC volunteering, study
  visit, job shadowing, seminar, conference). The extraction will be
  discarded.

Fields:
- `name`: the project's proper title, from the header's "Title" line (titles
  are already English); strip surrounding quotes and emoji.
- `country`: ISO-3166 alpha-2 of the HOST country, copied from the header's
  host line (already given as a code, e.g. HR).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD) of the ACTIVITY
  itself, from the body prose (e.g. "Dates (including travel days): 17-25
  August 2026"). The header's "Project dates" line usually lacks the year —
  take the year from the body, or failing that from the application deadline
  (the activity always starts on or after the deadline). If activity dates
  and travel dates are stated separately, use the ACTIVITY dates; if only a
  single range "including travel days" is given, use that range. If NEITHER
  the body nor an attached PDF states the dates, do NOT invent a
  plausible-looking range — set `format` to "other" instead (an event whose
  dates cannot be stated is unusable and must be discarded).
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached (participating-countries
      list, group-leaders table, or travel-reimbursement/budget table with
      one row per sending country).
    * SECONDARY SOURCE — the post prose, where countries are actually NAMED.
      Vague phrases like "young people from Europe, the Middle East, and the
      South Mediterranean" or "participants from different countries" name
      NOBODY.
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder
  or bloc codes such as "XX", "EU", "EUR", or "INT". If neither the PDF nor
  the text NAMES specific countries, `partner_countries` MUST be null — a
  null here is a correct, expected answer, not a failure.
- `description`: 80–160 word English summary covering the topic, target
  group, dates, host location, and anything practical (working language,
  participant age range, costs covered, number of Turkish places, how to
  apply). Use the post's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best
guess — post-validation will drop obviously broken extractions.
"""


def _http_get(url: str) -> str | None:
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logging.warning("youthist: GET failed url=%s err=%s", url, exc)
        return None
    if response.status_code != 200:
        logging.warning(
            "youthist: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    return response.text


def _detail_urls() -> list[str]:
    """Detail-page URLs from the dynamic apply-for-projects sitemap, found
    via the sitemap index (its filename embeds a rotatable Wix hash)."""
    index_xml = _http_get(_SITEMAP_INDEX_URL)
    if index_xml is None:
        return []
    m = _DYNAMIC_SITEMAP_RE.search(index_xml)
    if m is None:
        logging.warning(
            "youthist: dynamic apply-for-projects sitemap not found in index",
        )
        return []
    child_xml = _http_get(m.group(0))
    if child_xml is None:
        return []
    return [
        u for u in _SITEMAP_LOC_RE.findall(child_xml)
        if "/apply-for-projects/" in u
    ]


def _warmup_records(page_html: str) -> list[dict]:
    """The "News" CMS collection records embedded in a detail page's
    wix-warmup-data script. [] when the SSR format changed."""
    m = _WARMUP_SCRIPT_RE.search(page_html)
    if m is None:
        logging.warning("youthist: no wix-warmup-data script on detail page")
        return []
    try:
        data = json.loads(m.group(1))
        news = (
            data["appsWarmupData"]["dataBinding"]["dataStore"]
            ["recordsByCollectionId"]["News"]
        )
    except (ValueError, KeyError, TypeError) as exc:
        logging.warning("youthist: warmup JSON shape changed err=%s", exc)
        return []
    records = list(news.values()) if isinstance(news, dict) else list(news)
    return [r for r in records if isinstance(r, dict) and r.get("_id")]


def _first(value) -> str | None:
    """Wix multi-value CMS fields (status, projectType) arrive as lists."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _walk_richcontent(node, texts: list[str], files: list[dict]) -> None:
    if isinstance(node, dict):
        text_data = node.get("textData")
        if isinstance(text_data, dict):
            texts.append(text_data.get("text") or "")
        file_data = node.get("fileData")
        if isinstance(file_data, dict):
            files.append(file_data)
        for key, value in node.items():
            if key != "textData":
                _walk_richcontent(value, texts, files)
    elif isinstance(node, list):
        for value in node:
            _walk_richcontent(value, texts, files)


def _info_pack(record: dict, files: list[dict]) -> tuple[str | None, int | None]:
    """(url, size_bytes) of the record's own info-pack PDF, or (None, None).

    The `infopack` CMS field wins when set; otherwise the first PDF fileData
    node in the record's richcontent. Both resolve to the public
    /_files/ugd/<id>.pdf path (verified 200, application/pdf). Only this
    record's own nodes are considered — the raw page HTML also embeds other
    projects' PDFs.
    """
    m = _WIX_DOCUMENT_RE.search(record.get("infopack") or "")
    if m:
        return f"{_BASE}/_files/{m.group(1)}", None
    for file_data in files:
        if (file_data.get("type") or "").lower() != "pdf":
            continue
        src = file_data.get("src") or {}
        file_id = src.get("id") if isinstance(src, dict) else None
        if file_id:
            return f"{_BASE}/_files/{file_id}", file_data.get("size")
    return None, None


def _llm_content(record: dict, prose: str) -> str:
    """Authoritative CMS fields as a structured header, then the body prose
    extracted from the richcontent node tree."""
    location = record.get("location") or {}
    host = ", ".join(p for p in (
        location.get("formatted"), location.get("country"),
    ) if p) or "(not set)"
    return (
        f"Title: {record.get('title') or ''}\n"
        f"Project type: {_first(record.get('projectType')) or '(not set)'}\n"
        f"Host: {host}\n"
        f"Application deadline: "
        f"{record.get('deadlineForApplication') or '(not set)'}\n"
        f"Project dates (year-less): {record.get('projectDates') or '(not set)'}\n"
        f"\n{prose}"
    )


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    urls = _detail_urls()
    if not urls:
        return []

    records: list[dict] = []
    for url in urls[:3]:  # any detail page embeds the full collection
        page_html = _http_get(url)
        if page_html is None:
            continue
        records = _warmup_records(page_html)
        if records:
            break
    if not records:
        return []
    if len(urls) > len(records):
        logging.warning(
            "youthist: sitemap lists %d detail pages but warmup collection "
            "has %d records — collection may paginate now",
            len(urls), len(records),
        )

    candidates = {f"{_ID_PREFIX}{r['_id']}": r for r in records}
    seen = seen_ids(candidates.keys())
    fresh = {eid: r for eid, r in candidates.items() if eid not in seen}
    logging.info(
        "youthist: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, record in fresh.items():
        # Pre-filters on the CMS's own machine fields — the Completed backlog
        # and expired calls never cost a detail fetch or an LLM call. Both are
        # non-retryable: status only moves forward, deadlines never un-pass.
        status = _first(record.get("status"))
        if status != _STATUS_OPEN:
            logging.info(
                "youthist: skipping %s (status=%s)", event_id, status,
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue
        deadline_raw = record.get("deadlineForApplication")
        if deadline_raw:
            try:
                deadline = datetime.strptime(
                    deadline_raw[:10], "%Y-%m-%d",
                ).date()
            except ValueError:
                deadline = None  # malformed CMS date — extract anyway
            if deadline is not None and deadline < today:
                logging.info(
                    "youthist: skipping %s (deadline passed on %s)",
                    event_id, deadline.isoformat(),
                )
                mark_skipped(event_id, ADAPTER_NAME, "deadline_passed")
                continue

        texts: list[str] = []
        files: list[dict] = []
        _walk_richcontent(record.get("richcontent") or {}, texts, files)
        prose = "\n".join(t for t in texts if t.strip())
        if not prose:
            logging.info("youthist: skipping %s (empty body)", event_id)
            continue  # transient-ish: body may fill in later

        pdf_bytes: bytes | None = None
        info_pack, pdf_size = _info_pack(record, files)
        if info_pack and pdf_size is not None and pdf_size > _MAX_PDF_BYTES:
            logging.info(
                "youthist: info-pack for %s is %d bytes (over the %d limit), "
                "skipping download, text-only extraction (url=%s)",
                event_id, pdf_size, _MAX_PDF_BYTES, info_pack,
            )
        elif info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "youthist: info-pack fetch failed for %s, falling back "
                    "to text-only extraction (url=%s)",
                    event_id, info_pack,
                )
        else:
            logging.info("youthist: no info-pack for %s", event_id)

        extracted = extract(
            EXTRACTION_PROMPT, _llm_content(record, prose), pdf_bytes=pdf_bytes,
        )
        if extracted is None:
            continue  # validator already logged the reason

        fmt = extracted["format"]
        source = _FORMAT_TO_SOURCE.get(fmt)
        if source is None:
            logging.info(
                "youthist: skipping %s (format=%s, name=%r)",
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
                "youthist: skipping %s (already ended on %s)",
                event_id, extracted["period_end"],
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        link_path = record.get("link-news-title") or ""
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
            "url": f"{_BASE}{link_path}" if link_path else _BASE,
            "raw": {
                "youthist_id": event_id[len(_ID_PREFIX):],
                "status": status,
                "project_type": _first(record.get("projectType")),
                "deadline": record.get("deadlineForApplication"),
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("youthist: returning %d items", len(items))
    return items
