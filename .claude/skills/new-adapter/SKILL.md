---
name: new-adapter
description: Playbook for adding a new NGO/source adapter to backend/adapters/ — source recon, ingestion-strategy decision tree, adapter code conventions, and the verification gate. Use when the user wants to add a new event source (an NGO website URL) to the ingestion pipeline.
argument-hint: <url of the candidate NGO site>
---

# New source adapter

You are adding one self-contained source integration under `backend/adapters/`.
The workflow has two **hard checkpoints** where you STOP and wait for the user:
after recon (go/no-go on the strategy) and after local verification (review of
sample extractions before the PR). You never merge, never deploy, and never
touch Azure.

Read 2–3 existing adapters before writing anything — every module docstring is
a mini design doc recording why that ingestion strategy won. The pattern
exemplars are listed in Phase 2.

---

## Phase 1 — Recon (ends with a STOP)

Goal: pick the cheapest reliable ingestion strategy. Probe in this order and
take the **first** one that works — do not scrape HTML when an API exists.

1. **Tribe Events API** (ADEL pattern, `adapters/adel.py`):
   `GET <site>/wp-json/tribe/events/v1/events?per_page=50`
   Best case: machine `start_date`/`end_date`, venue country, category slugs
   that pre-classify YE vs TC, upcoming-only by default.
2. **WP core API + curated category** (YIC/Erasmusgram pattern, `adapters/yic.py`,
   `adapters/erasmusgram.py`): list `GET <site>/wp-json/wp/v2/categories?per_page=100`,
   find the open-calls bucket, then `GET /wp-json/wp/v2/posts?categories=<id>&per_page=50`.
   Check **category separation quality**: does the category hold only open
   calls, or also retrospectives/duplicates? Full body arrives in
   `content.rendered` — one request per cycle, no detail fetches.
3. **Category RSS with full bodies** (EYC/Európsky Dialóg pattern,
   `adapters/eyc_breclav.py`, `adapters/europsky_dialog.py`): a category feed
   whose items carry `content:encoded`. Prefer the **narrowest** feed that is
   open-calls-only (Mladiinfo lesson: the broad feed wastes Gemini quota on
   posts classified `other`).
4. **RSS excerpts + detail-page fetch** (Mladiinfo pattern, `adapters/mladiinfo.py`).
5. **HTML listing + detail pages** (BFY/YYSK/SALTO pattern, `adapters/bfy.py`,
   `adapters/yysk.py`, `adapters/salto.py`) — last resort.

**RSS trap check (YYSK lesson):** before trusting any feed, read 5+ items and
confirm they are OPEN CALLS, not past-event write-ups ("how it went"
retrospectives). A feed of retrospectives disqualifies option 3/4 even though
the feed technically exists.

### Recon checklist (answer every line)

- **robots.txt** — fetch it, quote the relevant rules, and honor them
  literally. SALTO lesson: a disallowed *query param* (`b_offset`) means the
  adapter must never emit it; find a compliant alternative (sort newest-first,
  raise the page size) rather than bending the rule. Note Content-Signals if
  present (one-shot inference with output linking back is compatible with
  `ai-train=no, use=reference` — see `adel.py`).
- **Volume & cadence** — how many open posts now; how far back does the
  listing/feed reach (backlog is fine — the `period_end` backstop marks ended
  items as skipped on the first run, it just costs one LLM call each).
- **Language(s)** of posts; the prompt will be bespoke to it.
- **Where the dates live** — machine fields, or prose the LLM must parse?
  Identify the exact label for ACTIVITY dates vs travel dates vs application
  deadline (Erasmusgram lesson: "Proje Tarihleri" not "Seyahat Tarihleri").
- **Info-pack style** — Google Drive file links / self-hosted PDFs / Canva /
  none. Identify APPLICATION-form links (docs.google.com/forms, forms.gle)
  that must NOT be matched as info-packs.
- **Deadline pre-filter** — does the listing expose an application deadline
  cheaply (YYSK card pattern)? If yes, skip expired calls without fetching
  detail pages or burning LLM calls.
- **Stable dedup id** — pick the most stable per-post key available: numeric
  WP/API id > guid > URL slug. Dedup id format is `<slug>:<stable-id>`.
- **Eligibility regime** — national NGO recruiting its own country's
  participants → `SENDING_COUNTRY = "<ISO2>"` (set is never empty). General /
  pan-European aggregator → `SENDING_COUNTRY = None` and events whose partner
  set can't be determined are DROPPED (`mark_skipped(...,
  "insufficient_eligibility")`) — accept loss over flooding (Phase 4f-B
  decision).

### STOP — recon report

Present: chosen pattern + endpoint/URL, dedup id scheme, date source, info-pack
rule, prompt language, eligibility regime, robots constraints, volume estimate,
and any risks (flaky site, ambiguous category, JS-rendered listing). Wait for
the user's go/no-go. If the site is a poor scraping candidate, say so plainly
and recommend skipping it.

---

## Phase 2 — Write the adapter

Branch first: `git checkout -b feat/ngo-<slug>` (never commit to main).

Copy the **nearest-pattern exemplar** as your starting point:

| Pattern | Exemplar |
|---|---|
| Tribe Events API | `adapters/adel.py` |
| WP core API + category | `adapters/erasmusgram.py` (national) / `adapters/yic.py` |
| RSS full-body | `adapters/europsky_dialog.py` |
| RSS + detail fetch | `adapters/mladiinfo.py` |
| HTML listing | `adapters/yysk.py` (deadline pre-filter) / `adapters/bfy.py` |

### Module contract

- Expose `fetch() -> list[tuple[str, dict]]` returning
  `("youth_exchange" | "training_course", item)` pairs, plus `EXTRACTION_PROMPT`,
  `ADAPTER_NAME`, and `SENDING_COUNTRY`.
- Item dict keys: `id`, `name`, `description`, `period_start`, `period_end`,
  `country`, `partner_countries`,
  `eligible_countries` (always via `events_writer.eligible_countries_for`),
  `url`, `raw` (include the source's own id, the info-pack URL, and the full
  `llm` extraction for debuggability).
- **Dedup before LLM**: build all candidate ids, call `events_writer.seen_ids()`
  once, and only process fresh ids. State lives in `events` +
  `skipped_sources` only — no extra ledger, no `last_fetched_at`.
- `mark_skipped(event_id, ADAPTER_NAME, reason)` **only for non-retryable
  decisions**: `format_<other>`, `already_ended`, `insufficient_eligibility`.
  Transient failures (HTTP error, PDF fetch failure, validator rejection)
  just `continue` — the item retries next cycle.
- Keep the `period_end < today` backstop even when a server-side filter
  already excludes past events.
- Fail soft everywhere: a network error returns `[]` with a `logging.warning`;
  the orchestrator's per-adapter try/except isolates failures, but don't rely
  on it for expected error paths.
- Write a **module docstring in the house style**: why this ingestion strategy
  won (and what it beat), the per-cycle flow, robots.txt findings, source
  quirks, and the closing "State lives in the `events` and `skipped_sources`
  tables — no extra ledger." line.
- Register the module in `adapters/__init__.py` (import + `ADAPTERS` append).
  New fetch deps go in `requirements.txt` (httpx, BeautifulSoup, feedparser
  are already vendored).

### Locked prompt rules (all mandatory, learned the hard way)

1. Attach the info-pack PDF via `pdf_fetcher.fetch_pdf` when present; on
   fetch failure log and fall back to text-only (never drop the item).
2. The prompt must **name where in the PDF to look** for partner countries:
   participating-countries list, group-leaders table, budget/reimbursement
   table with one row per sending country.
3. Canva links are not PDFs — do not match them; text-only extraction.
4. Application-form links (docs.google.com/forms, forms.gle) are never
   info-packs.
5. Online-only offerings (webinars, e-learning, free online courses) classify
   as `other` — no meaningful host country, not a KA1 mobility.
6. Never invent dates: if neither post nor PDF states activity dates, the
   prompt instructs `format: "other"` (an event without dates is unusable).
7. `partner_countries`: only countries actually NAMED, host excluded, real
   ISO-3166-1 alpha-2 only (no "XX"/"EU"/"INT"), and **null is a correct,
   expected answer** — say so in the prompt or the model will hallucinate.
8. `name` is the project's proper title (usually English, given in the body),
   not the recruiting headline; `description` is an 80–160 word English
   summary using only the post's own facts.
9. Non-English sources get a translation aid in the prompt (country-name →
   ISO-2 table, month names → numbers) — see `erasmusgram.py`; mark it as a
   translation aid, NOT a candidate list to copy from.

---

## Phase 3 — Verify locally (ends with a STOP)

Environment: `backend/.venv` + `local.settings.json` secrets (see
`backend/CLAUDE.md`). Write a throwaway script in the scratchpad (not the
repo) that imports the adapter and runs `fetch()` with logging at INFO.

Required checks (from the plan's Phase-4 test protocol):

1. Sample fetch succeeds; log shows `N listed / N already seen / N fresh`.
2. At least one item goes through `llm_extractor.extract` and passes schema +
   validation (`period_start ≤ period_end`, ISO-2 host, required fields
   non-null). Eyeball the extraction against the actual post: host country,
   activity dates (not travel dates), name, partner list.
3. A non-target post (retrospective, ESC, online course — find one if the
   source has any) classifies as `other` and is marked skipped.
4. **Dedup**: run `fetch()` a second time — zero LLM calls (everything lands
   in `seen_ids`).
5. If robots.txt constrained anything, assert the emitted URLs comply.
6. `python -m py_compile` the new module and confirm `adapters/__init__.py`
   imports cleanly.

### STOP — verification report

Show the user: the run counts, every skip decision with its reason, and 1–2
full extracted dicts next to links to the source posts. Wait for review.

---

## Phase 4 — PR (never merge, never deploy)

- Commit as `feat(adapters): add <name> (<ordinal> <country> source)` —
  conventional commits, present-tense imperative, review `git status` +
  `git diff` before staging.
- PR body: recon summary (strategy + what it beat), the locked-rule
  compliance notes, and the verification transcript.
- Deployment, merging, and the first-prod-cycle audit (checking
  `skipped_sources` reasons and inserted rows after the next hourly run) are
  the **user's** job — remind them, don't do them.
- If the source taught a new reusable lesson (a new pattern, a new trap),
  propose adding it to this skill and to `docs/plan.md` §4 so the next
  adapter benefits.
