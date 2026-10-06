"""GO Alive (goalive.eu) adapter: first Greek NGO. Ingests youth exchanges and
training courses from the site's own hand-rolled JSON API.

The opportunities page renders nothing but a "Loading projects…" placeholder,
which looks like a mandate to scrape HTML. It isn't — the page's own loader
(`/go-alive-opportunities/script.js`) reveals a bespoke PHP endpoint:

    GET /api_erasmus_projects.php?action=list          -> all current projects
    GET /api_erasmus_projects.php?action=get&id=<n>     -> one full record

which beats every option in the decision tree, including the WP core API this
site also exposes (its `erasmus-projects` category holds blog write-ups, not
the calls). The list payload gives us:

  - stable numeric `id` -> dedup id `goalive:<id>`;
  - machine ISO `start_date` / `end_date` — no date prose to parse, and no
    activity-vs-travel-date ambiguity to get wrong;
  - `type_of_project` ("Youth Exchange", "Training Course", "Advance Planning
    Visit") which pre-classifies the event, so the LLM does no routing;
  - host `country` / `city`.

The detail record adds the long-form prose the summary needs: `about`,
`accommodation`, `travel_reimbursement`, `participation_cost`, `language`, plus
`link` — the Google Form application URL, which is never an info-pack — and
`additional_blocks_html`, a free-form HTML section the detail page renders
verbatim under the prose. It is empty on every record so far, but it is where
an organiser would paste an info-pack link or a participant list, so it is
both scanned for info-packs and handed to the model as text.

Because type and dates arrive as machine fields, both cheap filters run BEFORE
any LLM call: out-of-scope types (APV, ESC, seminars) are dropped straight off
the list payload without even fetching their detail record, and the
already-ended backstop uses the API's own dates. The LLM keeps a narrow job —
ISO-2 host mapping, the English summary, and `partner_countries`.

Source quirks:
  - Closed projects are removed: `action=get` on a retired id returns
    `success: false`. No historical backlog, so a first run costs only as many
    LLM calls as there are open projects (5 at the time of writing).
  - The payload leaks whitespace and stray quoting — `" Romania"`, `"Italy "`,
    `"''Digital well-being''"` — so every string is stripped on ingest.
  - `id` is a string in the list payload and an int in the detail payload.
  - `about` sometimes opens by introducing the COORDINATING organisation and
    its home country ("Babilon Travel NGO … based in Cluj-Napoca, Romania")
    for a project hosted elsewhere; the prompt calls that out explicitly so
    the model doesn't emit it as a partner country.
  - No info-packs exist today (the only link in any record is the application
    form), but the Drive/PDF detection is wired for when one appears.

robots.txt: only `/wp-admin/` is disallowed (`admin-ajax.php` re-allowed); the
API path is unrestricted and no Content-Signals are published. The endpoint is
unversioned and undocumented, so `success` is checked, expected keys are
validated, and any shape change fails soft to an empty list.

State lives in the `events` and `skipped_sources` tables — no extra ledger.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from events_writer import eligible_countries_for, mark_skipped, seen_ids
from llm_extractor import extract
from pdf_fetcher import fetch_pdf

ADAPTER_NAME = "goalive"

# Greek NGO source — GR folded into every event's eligibility set (Phase 4f-B
# national-adapter regime); see eyc_breclav for the rationale. Confirmed by the
# reimbursement prose: "Travel expenses for participants from Greece are
# reimbursed up to 239€".
SENDING_COUNTRY = "GR"

_API_BASE = "https://goalive.eu/api_erasmus_projects.php"
_LIST_URL = f"{_API_BASE}?action=list"
_DETAIL_URL = f"{_API_BASE}?action=get&id={{project_id}}"

# Public detail page, built the way the site's own listing builds it so the
# link we push matches the one a visitor would click.
_PAGE_URL = "https://goalive.eu/go-alive-opportunities/project.html?id={project_id}-{slug}"

_ID_PREFIX = "goalive:"

# `type_of_project` (lowercased) → events.source bucket. Anything else —
# "Advance Planning Visit", ESC, seminars — is out of scope and skipped
# permanently, without fetching its detail record.
_TYPE_TO_SOURCE = {
    "youth exchange": "youth_exchange",
    "training course": "training_course",
}

_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Info-pack candidates: a Drive share link or a direct PDF. Application forms
# (docs.google.com/forms, forms.gle), Canva designs and Padlet boards are never
# info-packs, so they are excluded rather than matched.
_INFO_PACK_RE = re.compile(
    r"https://drive\.google\.com/file/d/[A-Za-z0-9_-]+[^\"'\s<>]*"
    r"|https?://[^\"'\s<>]+\.pdf(?:\?[^\"'\s<>]*)?"
)
_NEVER_INFO_PACK = ("docs.google.com/forms", "forms.gle", "canva.com", "padlet.com")

# Prose fields, in the order they are handed to the model.
_PROSE_FIELDS = (
    ("About the project", "about"),
    ("City", "city_description"),
    ("Accommodation and meals", "accommodation"),
    ("Travel reimbursement", "travel_reimbursement"),
    ("Participation cost", "participation_cost"),
    ("Working language", "language"),
)


EXTRACTION_PROMPT = """\
You extract a single Erasmus+ mobility event from an English-language project
record published by the Greek NGO GO Alive (https://goalive.eu). GO Alive is
the SENDING organisation: it recruits participants from Greece for projects
that are usually hosted and coordinated by a partner organisation abroad.

The input starts with a structured header (activity type, exact dates, venue)
taken from the site's own project API — treat those header values as
authoritative and copy them into the corresponding fields. The sections after
the header are the organiser's own prose. An info-pack PDF is occasionally
attached to this request; when present, prefer it for `partner_countries` —
the participating countries usually appear as a list or a group-leaders table
near the start, and the travel-reimbursement table (one row per sending
country) near the end. Do NOT invent data.

Fields:
- `format`: copy the "Activity type" value from the header verbatim
  ("youth_exchange" or "training_course"). Do not classify — the source has
  already guaranteed the type.
- `name`: the project title from the header. Strip decorative quoting the
  source sometimes adds (''Digital well-being'' -> Digital well-being) and
  trim surrounding whitespace. Titles are already English; keep the wording.
- `country`: ISO-3166 alpha-2 of the HOST country from the header's venue
  line (e.g. "Vatra Dornei, Romania" -> RO).
- `period_start`, `period_end`: ISO dates (YYYY-MM-DD), copied from the
  header's dates line.
- `partner_countries`: ISO-3166 alpha-2 codes of the OTHER participating
  countries, with the HOST EXCLUDED.
    * PRIMARY SOURCE — the info-pack PDF if attached.
    * SECONDARY SOURCE — the prose. The "Travel reimbursement" section is the
      most reliable place: it names sending countries and their per-country
      amounts ("Travel expenses for participants from Greece are reimbursed up
      to 239€"). The "About the project" section sometimes names the national
      teams inline.
    * CRITICAL — the "About the project" section often opens by introducing
      the COORDINATING organisation and the city/country where THAT
      organisation is registered ("Babilon Travel NGO is a non-governmental
      organisation based in Cluj-Napoca, Romania") for a project hosted in a
      DIFFERENT country. An organisation's own registered country is NOT a
      participating country. Only count a country when the text says
      participants come FROM it, or when it appears in an explicit
      "Participant countries:" list.
    * INCLUDE GREECE (GR) whenever the record shows Greek participants taking
      part — a partner list naming "GO Alive from Greece", a reimbursement
      line for participants from Greece, or a stated Greek group size. GO
      Alive being the sending organisation does NOT make Greece exempt: it is
      a participating country like any other and belongs in this list. Omit
      GR only when the host country IS Greece.
  Use only real ISO-3166-1 alpha-2 country codes. NEVER output placeholder or
  bloc codes such as "XX", "EU", "EUR", or "INT". If the record only says
  "young people from different European countries" without naming any, return
  null. Returning null is a correct and expected answer here — most of this
  source's records name no partner beyond Greece.
- `description`: 80–160 word English summary covering the topic, target group,
  dates, host location, and anything practical (working language, costs
  covered, travel reimbursement, participation fee, how to apply). Use the
  record's own facts; do not embellish.

If a required field is genuinely missing or ambiguous, return your best guess
— post-validation will drop obviously broken extractions.
"""


def _get_json(url: str) -> dict | None:
    """GET `url` and return the decoded object, or None on any failure."""
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        logging.warning("goalive: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "goalive: non-200 url=%s status=%d", url, response.status_code,
        )
        return None
    try:
        payload = response.json()
    except ValueError as exc:
        logging.warning("goalive: bad JSON url=%s err=%s", url, exc)
        return None

    if not isinstance(payload, dict):
        logging.warning("goalive: unexpected payload type url=%s", url)
        return None
    if not payload.get("success"):
        # Retired project, or the endpoint changed shape. Either way: not ours.
        logging.info("goalive: API reported success=false url=%s", url)
        return None
    return payload


def _clean(value: object) -> str:
    """Strip the whitespace and stray decorative quoting the API leaks."""
    if not isinstance(value, str):
        return ""
    return value.strip().strip("'\"").strip()


def _slug(name: str) -> str:
    """Mirror the site's own slug builder so our link matches theirs."""
    return re.sub(r"[^a-z0-9]+", "-", _clean(name).lower()).strip("-")


def _source_for(project: dict) -> str | None:
    """Map `type_of_project` to an events.source bucket, or None when the
    activity is out of scope (Advance Planning Visit, ESC, seminar, ...)."""
    return _TYPE_TO_SOURCE.get(_clean(project.get("type_of_project")).lower())


def _info_pack_url(project: dict) -> str | None:
    """First Drive/PDF link across the prose fields and the raw extra-blocks
    HTML (links there live in `href`s), or None. `link` is deliberately
    excluded — it always holds the application form."""
    blob = "\n".join(
        [_clean(project.get(key)) for _, key in _PROSE_FIELDS]
        + [_clean(project.get("additional_blocks_html"))]
    )
    for match in _INFO_PACK_RE.finditer(blob):
        url = match.group(0)
        if not any(host in url for host in _NEVER_INFO_PACK):
            return url
    return None


def _llm_content(project: dict, source: str) -> str:
    """Authoritative API fields as a structured header, then the prose."""
    venue = ", ".join(
        p for p in (_clean(project.get("city")), _clean(project.get("country"))) if p
    ) or "(venue not set)"

    lines = [
        f"Title: {_clean(project.get('project_name'))}",
        f"Activity type: {source}",
        f"Dates: {_clean(project.get('start_date'))} to "
        f"{_clean(project.get('end_date'))}",
        f"Venue: {venue}",
        f"Topic: {_clean(project.get('short_description'))}",
    ]
    for label, key in _PROSE_FIELDS:
        text = _clean(project.get(key))
        if text:
            lines.append(f"\n{label}:\n{text}")
    extra_html = _clean(project.get("additional_blocks_html"))
    if extra_html:
        extra = BeautifulSoup(extra_html, "html.parser").get_text("\n", strip=True)
        if extra:
            lines.append(f"\nAdditional information:\n{extra}")
    return "\n".join(lines)


def fetch() -> list[tuple[str, dict]]:
    """Return a list of (source, item) pairs ready for upsert_events.

    `source` is either "youth_exchange" or "training_course"; the caller is
    responsible for batching by source when writing to Supabase.
    """
    payload = _get_json(_LIST_URL)
    if payload is None:
        return []
    listed = payload.get("projects")
    if not isinstance(listed, list):
        logging.warning("goalive: list payload had no `projects` array")
        return []
    if not listed:
        logging.info("goalive: API returned 0 open projects")
        return []

    candidates = {
        f"{_ID_PREFIX}{_clean(p.get('id')) or p.get('id')}": p
        for p in listed
        if isinstance(p, dict) and p.get("id") is not None
    }
    seen = seen_ids(candidates.keys())
    fresh = {eid: p for eid, p in candidates.items() if eid not in seen}
    logging.info(
        "goalive: %d listed, %d already seen, %d fresh",
        len(candidates), len(seen), len(fresh),
    )

    today = date.today()
    items: list[tuple[str, dict]] = []
    for event_id, listing in fresh.items():
        project_id = event_id[len(_ID_PREFIX):]

        # Cheap filter #1 — the list payload already carries the activity type,
        # so out-of-scope activities cost neither a detail request nor an LLM
        # call.
        source = _source_for(listing)
        if source is None:
            raw_type = _clean(listing.get("type_of_project")) or "none"
            logging.info(
                "goalive: skipping %s (out-of-scope type: %r)",
                event_id, raw_type,
            )
            mark_skipped(event_id, ADAPTER_NAME, "type_out_of_scope")
            continue

        # Cheap filter #2 — the API's dates are machine data, so the
        # already-ended backstop also runs before the LLM. The endpoint only
        # serves current projects, but keep the backstop for consistency.
        period_start = _clean(listing.get("start_date"))
        period_end = _clean(listing.get("end_date"))
        try:
            end = datetime.strptime(period_end, "%Y-%m-%d").date()
        except ValueError:
            logging.info(
                "goalive: skipping %s (unparseable end_date %r)",
                event_id, period_end,
            )
            continue  # malformed API date — retry next cycle
        if end < today:
            logging.info(
                "goalive: skipping %s (already ended on %s)", event_id, period_end,
            )
            mark_skipped(event_id, ADAPTER_NAME, "already_ended")
            continue

        detail_payload = _get_json(_DETAIL_URL.format(project_id=project_id))
        if detail_payload is None:
            continue  # transient, or the project was just retired — retry
        project = detail_payload.get("project")
        if not isinstance(project, dict):
            logging.warning(
                "goalive: detail payload for %s had no `project` object", event_id,
            )
            continue

        pdf_bytes: bytes | None = None
        info_pack = _info_pack_url(project)
        if info_pack:
            pdf_bytes = fetch_pdf(info_pack)
            if pdf_bytes is None:
                logging.info(
                    "goalive: info-pack fetch failed for %s, falling back to "
                    "text-only extraction (url=%s)",
                    event_id, info_pack,
                )

        extracted = extract(
            EXTRACTION_PROMPT, _llm_content(project, source), pdf_bytes=pdf_bytes,
        )
        if extracted is None:
            continue  # validator already logged the reason

        items.append((source, {
            "id": event_id,
            "name": extracted["name"],
            "description": extracted["description"],
            # API dates are authoritative machine data — use them directly
            # rather than the LLM's copy (which the prompt asks to mirror).
            "period_start": period_start,
            "period_end": period_end,
            "country": extracted["country"],
            "partner_countries": extracted["partner_countries"],
            "eligible_countries": eligible_countries_for(
                extracted["country"],
                extracted["partner_countries"],
                SENDING_COUNTRY,
            ),
            "url": _PAGE_URL.format(
                project_id=project_id, slug=_slug(project.get("project_name")),
            ),
            "raw": {
                "goalive_id": project_id,
                "type_of_project": _clean(project.get("type_of_project")),
                "city": _clean(project.get("city")),
                "api_country": _clean(project.get("country")),
                "application_url": _clean(project.get("link")) or None,
                "info_pack_url": info_pack,
                "llm": extracted,
            },
        }))

    logging.info("goalive: returning %d items", len(items))
    return items
