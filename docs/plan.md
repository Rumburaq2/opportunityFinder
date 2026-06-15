# DiscoverEU Platform Expansion — Architecture & Phased Plan

## Context

Today the repo is a single Python Azure Function App (`discovereu-monitor/`) on a Consumption plan that hourly scrapes the DiscoverEU Elasticsearch API, dedups via a SHA256-hashed JSON blob in Azure Storage (`state.py`), and sends one Telegram message to a single hardcoded chat (`TELEGRAM_CHAT_ID`).

The goal is to grow this into a public platform:
- Free **browse** of DiscoverEU meet-ups + NGO-hosted Youth Exchanges
- Paid **filtered Telegram notifications** (event type, country, date range)
- Hard constraint: **$0/month** infra for a few hundred users

Confirmed decisions: Telegram-only channel, single flat monthly Stripe subscription (price set later via env), Supabase Auth (email + password), hardcoded NGO RSS source list, monorepo layout, Telegram linking via bot deep-link with one-time token, free tier = browse + 1 active filter, events stored in Supabase (written by Function App, read by frontend), reuse the existing Telegram bot, Supabase EU/Frankfurt region.

## Repo layout (final)

Strict 2-folder split at the repo root, each independently deployable:

```
/backend/   — Python Azure Function App  (scraper, dispatcher, telegram bot)
              ↑ renamed from discovereu-monitor/ via `git mv`, preserves history
/web/       — Next.js app on Azure Static Web Apps
              Stripe endpoints live here as Next.js API routes (single Node runtime)
/supabase/  — SQL migrations, version-controlled
```

No third `/api/` folder and no separate SWA managed Functions — all server-side code for the website lives inside `/web` as Next.js routes. Two GitHub Actions workflows, one per folder; each can use a `paths:` filter so a frontend change doesn't trigger a backend redeploy.

## Tech stack (final)

- **Frontend + payment endpoints**: Next.js (App Router, TS) on **Azure Static Web Apps** (free tier). Stripe checkout / portal / webhook are **Next.js API routes** under `/web/app/api/stripe/...` — no separate SWA managed Functions.
- **Backend (existing, extended)**: Python Azure Function App on Consumption plan, in `/backend/`.
- **DB + Auth**: **Supabase** (Postgres + Auth) in EU (Frankfurt). Free tier: 500 MB DB, 50k MAU.
- **Payments**: **Stripe** Checkout + Customer Portal + webhooks.
- **Notifications**: existing Telegram bot, extended to multi-user.

## Architecture

```
Browser ──HTTPS──> Next.js on SWA  ──┬── Supabase JS (anon, RLS read) ──> Supabase (EU)
                                     ├── Next.js API routes:
                                     │     /api/stripe/checkout
                                     │     /api/stripe/portal
                                     │     /api/stripe/webhook    ◄── Stripe callbacks
                                     │     /api/telegram/webhook  ◄── Telegram bot updates
                                     └── Telegram deep-link  t.me/Bot?start=<token>

Azure Function App (Python, Consumption) — /backend/
   ONE hourly timer "hourly_run" — iterates source adapters, then dispatcher.
   Each step wrapped in its own try/except so one failure doesn't cascade.

     try:
         meetups = get_all_meetups()                       # existing scraper.py
         new_ids  = upsert_events(meetups, 'discovereu')   # events_writer.py
     except: log_and_continue
     for adapter in ADAPTERS:                              # adapters/__init__.py
         try:
             items = adapter.fetch()                       # e.g. adapters/inex_sda.py
             new_ids += upsert_events(items, adapter.SOURCE)
         except: log_and_continue                          # one bad adapter ≠ outage
     try:
         dispatch_pending()                                # dispatcher.py
     except: log_and_continue

   Phase 4c folds the existing DiscoverEU scrape into adapters/discovereu.py
   as a clean-up; until then it stays in its own try-block above.

Stripe   ──webhook──> Next.js API route ──service-role──> Supabase profiles.subscription_status
Telegram ──webhook──> Next.js API route ──service-role──> Supabase profiles.telegram_chat_id
                                                          (consumes telegram_link_tokens)
```

## Supabase schema (EU/Frankfurt project, RLS on every table)

- **profiles** — `id uuid PK → auth.users`, `telegram_chat_id bigint unique`, `stripe_customer_id text unique`, `subscription_status text` (`none|active|past_due|canceled`), `subscription_current_period_end timestamptz`. RLS: owner select/update.
- **events** — `id text PK` prefixed `discovereu:<id>` / `ngo:<feed>:<guid>`, `source text check (in 'discovereu','youth_exchange')`, `name`, `description`, `period_start date`, `period_end date`, `country text` (host country = where the event physically takes place), `partner_countries text[]` (added Phase 4; nullable, populated when the source exposes it — meet-ups stay null; filter UX deferred), `url text`, `raw jsonb`, `first_seen_at`, `last_seen_at`, `erasmus_project_ref text` (added Phase 4e; nullable; canonical Erasmus+ KA1 project reference number when extractable from the info pack), `cluster_id uuid` (added Phase 4e; nullable; groups cross-NGO postings of the same project). Indexes on `(source)`, `(country)`, `(period_start)`, composite `(source, country, period_start)`, GIN on `partner_countries`, `(cluster_id)`. RLS: public (anon + auth) `select`; no client writes.
- **subscriptions_filters** — `id uuid PK`, `user_id → profiles`, `event_type` (`any|discovereu|youth_exchange`), `country text null`, `date_from`, `date_to`, `active bool`. Index `(active, user_id)`. RLS: owner CRUD. Free-tier limit enforced by `BEFORE INSERT` trigger checking `count(active where user_id) < 1 OR profiles.subscription_status = 'active'`.
- **telegram_link_tokens** — `token text PK`, `user_id`, `expires_at` (now + 15 min), `consumed_at`. RLS: owner insert/select; service-role consumes.
- **notifications_sent** — composite PK `(user_id, event_id)`, `filter_id`, `sent_at`. Dedup ledger.
- **stripe_events_seen** — `event_id text PK`, `received_at`. Webhook idempotency.
- (Optional) **scrape_runs** — observability log.

Migrations checked into `supabase/migrations/`:
- `0001_events.sql`
- `0002_profiles_filters_tokens_notifications.sql`
- `0003_rls_policies.sql`
- `0004_free_tier_trigger.sql`

## Function App extensions (under `backend/`)

The existing `discovereu-monitor/` folder is renamed to `backend/` via `git mv` in the very first commit of Phase 1, preserving file history.

**Existing files kept untouched until validated changes land:**
- `scraper.py` — kept as-is. Continues to expose `fetch_meetups(year)`, `get_all_meetups()`, `compute_hash(meetups)`. No changes in Phase 1a.
- `notifier.py` — kept as-is in Phase 1a. Continues to expose `format_notification(new, old)` and `send_notification(message)` for the owner's existing alert. A new `send_to_user(chat_id, message)` is **added** (alongside the old function, not replacing) in Phase 3 when multi-user dispatch lands.
- `state.py` — kept as-is in Phase 1a. Continues to drive the owner's blob-storage dedup. Retired only in Phase 1b after Supabase shadow-write is verified.
- `function_app.py` — Phase 1a edit is **strictly additive**: existing `check_meetups` body unchanged; one new `try: upsert_events(meetups_1, 'discovereu') except: log` call appended after the existing `save_state` + `send_notification`. Owner's Telegram unaffected if Supabase fails.

**New files (zero risk to existing flow — they're new):**
- `supabase_client.py` — thin wrapper around `supabase-py` using `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. Exposes `get_client()`.
- `events_writer.py` — exposes `upsert_events(items: list[dict], source: str) -> list[str]`. Upserts into Supabase `events`; returns newly inserted ids via `RETURNING (xmax = 0) AS inserted`. (The id list isn't strictly needed for the dispatcher's robust query, but is useful for logging.)
- `adapters/` (Phase 4) — per-source adapter registry. `adapters/__init__.py` exposes `ADAPTERS: list` (each entry is an adapter module). Each adapter is a **self-contained source integration**: fetch logic + bespoke LLM prompt + dedup + per-source mapping. Each module exposes `fetch() -> list[dict]` (same shape as `scraper.get_all_meetups()`), a `SOURCE: str` constant, and a bespoke `EXTRACTION_PROMPT: str` tailored to the source's language, vocabulary, and format quirks. **Ingestion rule:** if the source's wire format is structured (REST API delivering JSON/XML against a stable schema, e.g. DiscoverEU), the adapter parses directly. Otherwise (NGO blog posts, RSS prose bodies, HTML pages, social posts), the adapter passes the post body to the shared `llm_extractor` for structured extraction. Earlier "no LLM in MVP" rule retired in 2026-05-10 cost analysis: Gemini Flash-Lite free tier (1k RPD) covers expected volume at $0/mo even across 30+ sources, the LLM call rate is bounded by *new-post* rate (not fetch rate), and LLM-based extraction removes per-source regex maintenance, makes new NGOs nearly cost-free to add, and unlocks `events.partner_countries` from narrative content that regex couldn't reach. **State**: each adapter constructs a stable source-prefixed id (e.g., `eyc:<rss_guid>`) and pre-checks the `events` table for existing ids before calling the LLM — `events` doubles as the dedup ledger, no separate state table, no blob storage, no `last_fetched_at`. Adapters added one NGO at a time (`adapters/eyc_breclav.py`, `adapters/<next>.py`, ...). Fetch tools differ per source (`feedparser`, `httpx + BeautifulSoup`, `icalendar`, etc.) and are added to `requirements.txt` per-adapter. A format change at one source breaks only that source's adapter — the prompt or fetch is updated; other adapters and the extractor are untouched.
- `llm_extractor.py` (Phase 4a) — extraction plumbing. Exposes `extract(prompt: str, content: str) -> dict | None`. Uses Gemini 2.5 Flash-Lite (free tier, 1k RPD, structured output via `response_schema`). Enforces a fixed JSON output schema matching the `events` shape — `name`, `country` (ISO-2), `period_start` (date), `period_end` (date), `partner_countries` (string[] or null), `description`, plus an `is_youth_exchange: bool` discriminator. Post-LLM validation: `period_start ≤ period_end`, dates within ±5y of today, all required fields non-null, and **country codes validated against a real ISO-3166-1 alpha-2 set** (added with Phase 4f). The earlier shape-only check (two uppercase letters) let LLM placeholder/bloc codes like `XX` and `EU` through into `partner_countries`; the set check rejects those while accepting every real country (incl. Kosovo `XK`) and normalising the common aliases `UK→GB` / `EL→GR`. Asymmetric handling by field: an invalid **host** `country` rejects the whole extraction (a course with a garbage host is broken), but invalid **partner** codes are individually **dropped** (→ `null` if none remain) so one junk code never discards an otherwise-valid course. This lives in the shared validator, so it protects every adapter, not just SALTO. Extractions failing validation return `None` and the adapter skips that item (it stays absent from `events` and reappears next cycle for retry — same robust-retry pattern the dispatcher already uses). No fallback provider: a Gemini outage just delays the next extraction by one hourly cycle, which is acceptable. Holds **no source-specific knowledge** — all source quirks live in the per-adapter `EXTRACTION_PROMPT`.
- `dispatcher.py` (Phase 3) — exposes `dispatch_pending()`. Invoked at the end of `hourly_run`, in the same Function execution. No separate timer. Algorithm:
  1. Query Supabase: events from last 7 days × active filters (event_type/country/date_from/date_to), joined to profiles where `telegram_chat_id IS NOT NULL`, **left-anti-joined** to `notifications_sent` (so already-sent rows are excluded).
  2. For each match: **send first** (call `notifier.send_to_user(chat_id, msg)`), then `INSERT INTO notifications_sent ... ON CONFLICT DO NOTHING`. If send fails, leave row absent — next hourly run picks it up automatically. No drops.
  3. Global `time.sleep(0.04)` between sends (under 30 msg/s); respect `429 retry_after`.

  Safety properties:
  - **No drops** — failed sends reappear in the query next cycle and retry.
  - **No duplicates in normal case** — `notifications_sent` row excludes the match.
  - **Rare theoretical duplicate** — if the process crashes between successful send and insert, the user gets one repeat next cycle. Acceptable for MVP.

(No `telegram_bot.py` in /backend/ — receiving bot updates is handled by a Next.js API route. Python only **sends** via the Bot API.)

- `state.py` — retired at end of Phase 1b (deleted + import removed from `function_app.py`).
- `requirements.txt` — add `supabase` (Phase 1a). Phase 4a: `+ google-generativeai` (Gemini SDK), `+ feedparser` (EYC adapter's fetch). Subsequent adapters add fetch-tool deps as needed (`lxml`, `httpx`, `icalendar`, etc.).

New env vars (Function App): `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (Phase 1a). Keep `TELEGRAM_BOT_TOKEN` (used for `sendMessage`). Rename existing `TELEGRAM_CHAT_ID` → `LEGACY_TELEGRAM_CHAT_ID` during transition; remove after Phase 3. Phase 4a adds `GEMINI_API_KEY` (LLM provider for extraction).

Env vars (SWA / Next.js): `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_BOT_TOKEN` (for verifying webhook + responding to `/start`), `TELEGRAM_WEBHOOK_SECRET` (verified against the `X-Telegram-Bot-Api-Secret-Token` header), `TELEGRAM_BOT_USERNAME` (used to render the deep-link), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`.

**Stripe endpoints live as Next.js API routes inside `/web`, not in the Python app and not as separate SWA managed Functions.** One runtime (Next.js/Node), unified env vars and logging with the rest of the site, `npm run dev` runs everything locally, and the Node Stripe SDK is the de-facto standard.

## Next.js app (`web/`)

Stack: Next.js App Router, TypeScript, `@supabase/ssr`, Tailwind + shadcn/ui. No state manager — RSC + server actions.

Routes:
- `/` — landing + featured events
- `/events`, `/events/[id]` — public browse (anon Supabase client; RLS public read). The detail-route segment is **base64url(events.id)**, not `encodeURIComponent(events.id)` — Azure SWA's URL normalizer decodes `%2F` and collapses consecutive `/` before routing, which breaks any id containing slashes (e.g. EYC ids embed the full WP URL). The list page generates the slug with `Buffer.from(e.id).toString("base64url")`; the detail page decodes back with `Buffer.from(params.id, "base64url").toString("utf-8")`. Future NGO adapters can put anything in `events.id` — the slug layer absorbs URL-safety concerns.
- `/login`, `/signup`, `/auth/callback` — Supabase email+password auth
- `/account` — profile + active filters
- `/account/filters/new`, `/account/filters/[id]` — server-action gated by `subscription_status` (free user with 1 active filter sees upgrade card; DB trigger is the backstop)
- `/account/link-telegram` — generates a `telegram_link_tokens` row, renders `https://t.me/<BotUsername>?start=<token>`
- `/account/billing` — Subscribe / Manage buttons
- `/api/stripe/checkout`, `/api/stripe/portal`, `/api/stripe/webhook` — **Next.js API routes** at `web/app/api/stripe/{checkout,portal,webhook}/route.ts`
- `/api/telegram/webhook` — **Next.js API route** at `web/app/api/telegram/webhook/route.ts`. Verifies `X-Telegram-Bot-Api-Secret-Token` header against `TELEGRAM_WEBHOOK_SECRET`; on `/start <token>` payload, looks up `telegram_link_tokens`, sets `profiles.telegram_chat_id`, marks token consumed; on `/stop`, clears chat_id.

Files (selected):
- `web/app/layout.tsx`, `web/app/page.tsx`
- `web/app/events/page.tsx`, `web/app/events/[id]/page.tsx`
- `web/app/(auth)/login/page.tsx`, `web/app/(auth)/signup/page.tsx`, `web/app/auth/callback/route.ts`
- `web/app/account/{page,filters/new/page,filters/[id]/page,link-telegram/page,billing/page}.tsx`
- `web/app/api/stripe/{checkout,portal,webhook}/route.ts`
- `web/app/api/telegram/webhook/route.ts`
- `web/lib/supabase/{server,client}.ts`, `web/lib/stripe.ts`, `web/lib/telegram.ts`
- `web/staticwebapp.config.json`

## Phased rollout (independently shippable)

1. **Phase 1 — Rename + introduce Supabase, strictly additive (1–2 d).** Split into two safer sub-steps:
   - **1a — Rename + shadow-write to Supabase.** First commit: `git mv discovereu-monitor backend`. Provision Supabase project (EU). Create `events` + `notifications_sent` tables. Add `supabase_client.py` and `events_writer.upsert_events()`. Modify `function_app.py` to **append** a `try: upsert_events(meetups_1, 'discovereu') except: log` call after the existing `save_state` + `send_notification`. Existing flow (scraper, blob state, owner Telegram) remains 100% intact and authoritative. Verify rows appear in Supabase and counts match the scraper's output.
   - **1b — Retire blob state.** After 1a runs cleanly for a few cycles, refactor `function_app.py` to drive the owner's notification off the new event ids returned by `upsert_events()` instead of the blob hash. Delete `state.py` and remove its imports. Drop the blob container in Azure. Keep `notifier.send_notification` for the owner one more phase; it will be replaced in Phase 3 by `send_to_user` once the owner becomes "first subscriber".
2. **Phase 2 — Public read-only browse site (2–3 d).** Bootstrap `web/`. Implement `/` and `/events` over Supabase anon read. No auth yet.
3. **Phase 3 — Auth + Telegram linking + 1 free filter for everyone (4–5 d).** Split into five safer sub-steps following the additive-then-retire pattern (mirrors 1a/1b):
   - **3a — Supabase schema + Next.js auth (no behavior change).** Add migrations `0002_profiles_filters_tokens.sql`, `0003_rls_policies.sql`, `0004_free_tier_trigger.sql` (BEFORE INSERT on `subscriptions_filters` checking `count(active where user_id) < 1 OR profiles.subscription_status='active'`). Wire `@supabase/ssr` into `web/lib/supabase/{server,client}.ts` + `web/middleware.ts`. Add `/login`, `/signup`, `/auth/callback`, read-only `/account`. New SWA env: `SUPABASE_SERVICE_ROLE_KEY`. Backend untouched; owner's hourly Telegram unaffected. Verify: sign up via live URL → `auth.users` + `profiles` row both exist → `/account` reflects login. Risk: low.
   - **3b — Telegram linking flow.** Add `/account/link-telegram` (generates token in `telegram_link_tokens`, renders `t.me/<bot>?start=<token>`). Add `/api/telegram/webhook` Next.js route — verifies `X-Telegram-Bot-Api-Secret-Token` against `TELEGRAM_WEBHOOK_SECRET`; on `/start <token>` looks up the token, sets `profiles.telegram_chat_id`, marks consumed; on `/stop` clears chat_id. Create dev bot via @BotFather for local testing; register prod webhook once with `setWebhook` + `secret_token`. New SWA env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_USERNAME`. Verify with a burner Telegram account; owner's notification path still untouched. Risk: medium — first time the prod bot has a webhook (it was send-only before); webhook misconfig fails silently for users but doesn't break sending.
   - **3c — Filter management UI.** Add `/account/filters/new` and `/account/filters/[id]`. Server actions for create/update/delete; the `0004` trigger from 3a is the DB-side backstop. No notifications yet — filters are stored but not consumed. Verify: free user creates 1 filter ✓, second insert blocked by trigger ✓, edit + delete work. Risk: low.
   - **3d — Dispatcher (additive: dual-send for owner).** Add `backend/dispatcher.py` with `dispatch_pending()` (left-anti-join `notifications_sent`, send-first-then-insert, `time.sleep(0.04)` between sends, 429 retry). Add `notifier.send_to_user(chat_id, msg)` alongside legacy `send_notification`. Append `dispatch_pending()` at end of `check_meetups`. Sign up owner on the live site, link Telegram, create one filter (`event_type=any`). Both paths now fire — owner receives duplicate messages each hourly cycle for several runs (mirrors backend v1→v2 cutover). Verify legacy + new produce the same set of meetups for ≥3 cycles before moving on. Risk: high — bug could spam; mitigation is that the only "user" at this stage is the owner.
   - **3e — Retire legacy notifier path.** Once 3d has run cleanly with matched output, drop the legacy `send_notification` call from `function_app.py`, delete `notifier.send_notification`, remove `LEGACY_TELEGRAM_CHAT_ID` / `TELEGRAM_CHAT_ID` env var on the v2 Function App. Owner now receives via dispatcher only. Risk: low; reversible by re-adding the call.
4. **Phase 4 — NGO ingestion via per-source adapters.** Per-NGO modules under `backend/adapters/`, each a self-contained source integration (fetch + bespoke LLM prompt + dedup + mapping). **Locked rules:** structured-wire-format sources (REST APIs delivering JSON/XML, e.g. DiscoverEU) parse directly; everything else (NGO blog posts, RSS prose bodies, HTML pages) goes through the shared `llm_extractor` with strict JSON-schema enforcement and post-LLM validation. LLM cost at our volume is effectively zero — only *new* posts trigger extraction (deduped against `events.id`), and Gemini Flash-Lite free tier (1k RPD) covers expected volume even across 30+ sources. Country-of-event = host country; `events.partner_countries text[]` (added in Phase 4a migration) captures participating countries when the LLM extracts them from narrative; otherwise null. Filter UX on `partner_countries` deferred to a later phase. Sub-steps:
   - **4a — LLM extractor + adapter framework + first NGO (EYC Břeclav).** Build `backend/llm_extractor.py` (Gemini 2.5 Flash-Lite, structured output, post-LLM validation). Build `backend/adapters/__init__.py` (`ADAPTERS = [eyc_breclav]`) and `backend/adapters/eyc_breclav.py`. EYC adapter: fetches the Czech category RSS at `https://eycb.eu/category/zahranicni-projekty/feed/` via `feedparser` (with `?paged=N` for catch-up if needed), constructs candidate ids `eyc:<rss_guid>`, queries `events` for existing ids, and for each new item passes `<content:encoded>` to `llm_extractor.extract(EYC_EXTRACTION_PROMPT, body)`. The prompt is bespoke to EYC: Czech-language hints, common patterns (`Klíčová Akce 1:`, `Termín konání:`, `Místo konání:`), Czech country-name → ISO-2 mapping examples, and an instruction to set `is_youth_exchange=false` for any non-Youth-Exchange post (Training Course, ESC placement, Strategic Seminar). Items where the LLM returns `is_youth_exchange=false` or `period_end < today` are skipped before upsert. `function_app.py` gains a loop over `ADAPTERS` with per-adapter try/except. Add migration `0007_events_partner_countries.sql` (`alter table events add column partner_countries text[]` + GIN index). New env var: `GEMINI_API_KEY`. Frontend: event-type filter chip on `/events` and the filter form.
   - **4b — Subsequent Czech NGOs.** Each new NGO = one new adapter module + one-line registry append. No framework changes. Shadow-validate each new source for a couple of cycles before exposing it via the public landing page or filter UI.
   - **4c — Cleanup: fold DiscoverEU into the registry.** Once the pattern is proven across 2–3 NGOs, move `function_app.py`'s top-level DiscoverEU scrape into `adapters/discovereu.py`. The orchestrator becomes a single loop over `ADAPTERS`. Pure refactor; no behavior change.
   - **4d+ — Other countries.** Same pattern, country-by-country. No framework changes; only new adapter modules.
   - **4e — Cross-NGO duplicate clustering.** Once a non-Czech adapter ships, the same Erasmus+ Youth Exchange will commonly be posted by multiple national sending NGOs — each adapter creates its own `events` row even though the underlying project is identical. Strategy is **cluster, don't merge**: keep one row per NGO post (each carries the national application channel, which actually matters to the user — you apply through your own country's NGO) and group them via `cluster_id`. Two complementary signals decide cluster membership: **(a) canonical** — the Erasmus+ KA1 project reference number (e.g. `2025-1-IT01-KA152-YOU-000123456`), extracted from the info pack by the LLM when a PDF is attached; **(b) heuristic, primary in practice** — same host country + overlapping dates + high name similarity (project names are arbitrary so unrelated collisions are rare; trigram similarity on `name` with pg_trgm is the simplest implementation). Heuristic carries most of the weight because info-pack PDFs are unevenly available (Canva, missing PDFs, posts before grant ID is issued). Migration `0008_events_cluster.sql` adds `events.erasmus_project_ref text` + `events.cluster_id uuid` + GIN index; a per-row trigger or post-upsert pass computes `cluster_id`. Dispatcher updated to send one message per cluster per user; browse UI shows one card per cluster with per-NGO application links. Build when the first real collision lands, not pre-emptively.
   - **4f — SALTO European Training Calendar (first international, aggregator source).** SALTO-YOUTH's [European Training Calendar](https://www.salto-youth.net/tools/european-training-calendar/) is a pan-European aggregator of Erasmus+ youth-worker mobility offers — unlike the Czech NGO feeds it is an official, English-language, structured directory covering all programme countries. It fills the `training_course` bucket only (the calendar is for youth workers, not teen participants — youth exchanges never appear here). Key differences from the NGO adapters, all exploited below: (i) the listing supports **server-side activity-type filtering** (`b_activity_type=4` = Training Course), so the adapter ingests a single pre-classified type and the LLM does **no classification** — `format` is hard-set to `training_course`, the `other`-dropping branch disappears, and the prompt loses its Czech classification + translation blocks; (ii) listings are **English and field-structured** (title, organiser, dates, "City, Country", application deadline, "participants from <countries>", summary) with a downloadable SALTO-hosted info-pack PDF on each detail page; (iii) detail URLs carry a **stable numeric id** (`/training/<slug>.<numericid>/`) → clean dedup id `salto:<numericid>`; (iv) **server-side date filters** replace post-hoc dropping — `b_begin_date_after_*` and `b_application_deadline_after_*` set to *today* (generated dynamically at fetch time) return only future events whose application window is still open, so closed-deadline noise never enters; (v) the **eligibility filter** `b_participating_countries=<ISO2>` is a **repeatable, OR-logic** param — one URL with many country values covers all of Europe in a single feed (no per-country adapter, ever). **robots.txt constraint:** `Disallow: /*?*b_offset*` forbids the pagination param, so the adapter must **never** emit `b_offset`; instead sort newest-first (`b_order=creation`) and crawl **page 1 only**, relying on hourly dedup (`seen_ids`) to catch new arrivals as they surface at the top. To absorb the existing backlog in one robots-compliant request, raise `b_limit` (not blocked by robots) rather than paginate — verify at build time that SALTO honours a large `b_limit` without an offset; if it caps it, accept page-1-only and let the backlog fill as offers are reposted. LLM extraction is **retained** (not a bespoke HTML parser) because volume is trivial, it normalises country names → ISO-2, excludes the host from `partner_countries`, reads the info-pack PDF, and survives SALTO layout drift — consistent with the locked "LLM over regex" rule for non-tabular sources. No schema change, no new dependency (`httpx` + `BeautifulSoup` already vendored by the `bfy`/`mladiinfo` adapters). Split into two independently shippable parts:
     - **4f-A — CZ-eligible ingestion (ship now).** One new module `backend/adapters/salto.py` exposing `fetch()` + `EXTRACTION_PROMPT`, appended to `ADAPTERS`. URL params: `b_activity_type=4`, `b_participating_countries=CZ` (single value — only courses a Czech participant can join, so **zero dispatch changes and zero noise** for the current Czech-only user base), dynamic `b_begin_date_after_*`/`b_application_deadline_after_*` = today, `b_order=creation`, no `b_offset`. Flow mirrors `bfy`: parse listing → numeric ids → `seen_ids` dedup → per fresh item GET detail page, extract body, find SALTO-hosted info-pack PDF (`pdf_fetcher.fetch_pdf`), run `llm_extractor.extract` with the simplified English prompt, route every result to the `training_course` bucket. Keep the `period_end < today` backstop + `mark_skipped(..., "already_ended")` for consistency even though the server filter already excludes past events. Verify: sample-fetch locally, run one item through the extractor and assert it passes schema + validation; confirm `events` rows land with `source='training_course'`; confirm a second cycle triggers no repeat LLM call (dedup); confirm the emitted URLs never contain `b_offset` (robots compliance).
     - **4f-A — CZ-eligible ingestion. ✅ SHIPPED 2026-06-13** (merged PR #21; commits `77f9bab` adapter, `3ea9f6b` per-cycle cap `_MAX_PER_CYCLE=10`, `c52cf10` real-ISO-3166 country validation in the shared extractor). See [[project_phase_4f_shipped]].
     - **4f-B — All-country onboarding + OPTIONAL per-filter eligibility. ✅ SHIPPED 2026-06-15** (PR #22: migration `0012_eligibility` = events.eligible_countries + profiles.home_country + subscriptions_filters.eligible_only + pending_notifications() gate; SALTO dropped its `b_participating_countries` filter; lazy home_country collection in the web filter form. Follow-up hotfix PR #23 `fix/extractor-pdf-fallback`: `llm_extractor` retries text-only when Gemini 400s on a corrupt/zero-page info-pack PDF, so an unparseable SALTO PDF no longer drops the whole event. See [[project_phase_4f_shipped]].) Original design: onboard users from any country and give them an *optional, additive* "only alert me for events I'm eligible for" control. **Key principle:** eligibility is **opt-in per filter**, NOT a mandatory gate — default OFF ⇒ current behavior is 100% unchanged (matching stays driven by the existing filter: `event_type` + host `country` + dates). SALTO is **not special** at match time; it's just another `training_course` source. The only per-source nuance is at *ingestion*, where each adapter populates the new `eligible_countries`. Supports the "I'm searching for a youth exchange for a friend in another country" case (leave the toggle off → see everything regardless of nationality). **Decision (2026-06-14): accept event loss over flooding** — general adapters DROP events whose participating set can't be determined rather than store them as open; **cross-source enrichment is explicitly out of scope** for 4f-B (it's fuzzy-match-prone, a wrong merge corrupts eligibility worse than a gap, and it can't recover "open to all / never-enumerated" courses anyway — revisit under 4e clustering when there are more overlapping sources).

       **Eligibility population — `NULL` means "declared open", set deliberately, NEVER as an extraction outcome.** Per adapter type, on a valid extraction:
         - **DiscoverEU** → `NULL` (declared open). Opt-in users correctly get these.
         - **National adapter** (Czech NGOs `eyc_breclav`/`bfy`/`mladiinfo`, `SENDING_COUNTRY='CZ'`) → `[host] + partners + sending_country`. Never empty (CZ guarantees it), so incomplete partner extraction can't hide a Czech-eligible event from Czech users.
         - **General adapter** (SALTO, future ones, `SENDING_COUNTRY=None`) → `[host] + partners` **only if partners were actually found. If not → DROP the event** (`mark_skipped(event_id, ADAPTER_NAME, "insufficient_eligibility")`, don't ingest). Measured failure rate at design time: SALTO ~22% (14/65) — accepted loss. Also fixes, for this case, the existing "validation miss retries forever / hogs a `_MAX_PER_CYCLE` slot" wart, since the decision is now recorded.

       **Data model (one migration, e.g. `0012_eligibility.sql`):**
       - `events.eligible_countries text[]` (nullable) + GIN index.
       - `profiles.home_country text` (nullable, ISO-2, light `~ '^[A-Z]{2}$'` check). Backfill existing rows → `'CZ'` (current users are Czech).
       - `subscriptions_filters.eligible_only boolean not null default false`.
       - Replace `pending_notifications()` (currently `0010`) adding ONE clause to the existing join (it already joins `profiles p`): `and (f.eligible_only = false or e.eligible_countries is null or p.home_country = any(e.eligible_countries))`.
       - **Backfill existing events** consistent with the go-forward rule (must be definitive — once a row is in `events`, `seen_ids` stops the adapter from ever reprocessing it): DiscoverEU rows stay `NULL`; Czech-NGO rows → `distinct([country]+partners+'CZ')`; SALTO rows WITH partners → `distinct([country]+partners)`; SALTO rows WITHOUT partners (~14) → DELETE from `events` + record in `skipped_sources` (`reason='insufficient_eligibility'`) so they aren't re-ingested (accepted loss).

       **Backend:** each adapter declares `SENDING_COUNTRY` and computes `eligible_countries`; general adapters call `mark_skipped(...,"insufficient_eligibility")` + skip when partners empty. `events_writer._row_for_ngo` passes `eligible_countries` through; `_row_for_discovereu` leaves it null. **Widen SALTO ingestion:** in `salto.py` **drop the `b_participating_countries` param entirely** (remove `_PARTICIPATING_COUNTRIES` from `_listing_url()`) so it ingests ALL training courses — relevance is now handled downstream by the user's host-country filter + optional eligibility. Bigger backlog absorbed by `_MAX_PER_CYCLE=10`.

       **Web (`web/`):** **No signup change** (keep it frictionless — no country field at signup). (a) `FilterForm` gains an "Only notify me for events I'm eligible for (from my country)" checkbox bound to `eligible_only` — **hidden when `event_type='discovereu'`** (eligibility is meaningless there; DiscoverEU is open to all). When the user checks it and `home_country` is unset, reveal an inline required country `<select>`; the filter-save action persists `home_country` to the profile AND `eligible_only` to the filter in the same submit (lazy collection — NOT at signup). (b) `/account` shows + edits `home_country` for users who want to change it later.

       **Rollout order (dependency-sensitive — PostgREST upsert errors on an unknown column):** 1) apply migration `0012` FIRST; 2) deploy backend (adapters write `eligible_countries`; SALTO drops the CZ filter) via `func ... publish`; 3) merge → web deploy (filter + account UI) via GH Actions. `eligible_only` defaults false + `home_country` backfilled to CZ ⇒ fully additive; nothing changes for existing users until they opt in. **Migration application:** via Supabase MCP `apply_migration` if the server's `--read-only` flag is removed, else paste the SQL into the Supabase dashboard (MCP token refreshed 2026-06-14; takes effect after session reload). **Verify:** columns + backfill correct (SALTO rows = host+partners or dropped, Czech rows include CZ, DiscoverEU null); a `home_country=DE` user with `eligible_only=true` only matches events where `DE = ANY(eligible_countries)`; `eligible_only=false` is unaffected; DiscoverEU always passes; free-tier 1-filter trigger still holds.
5. **Phase 5 — Stripe monetization (3–4 d).** Next.js API routes: checkout, portal, webhook (test mode first). DB free-tier trigger gated by `subscription_status`. Billing pages + upgrade CTAs. Flip to live mode behind env var.
6. **Phase 6 — Hardening & GDPR (as needed).** `scrape_runs` log, `/admin/health`, Telegram retry on `429`, account-deletion endpoint that cascades + cancels Stripe, App Insights alerts, privacy notice.

Branch per phase (`feat/phase-N-<slug>`), Conventional Commits per existing CLAUDE.md, never to main.

## Risks & cost-watch

- **Supabase free 500 MB DB / 50k MAU** — `notifications_sent` is the only growth vector; cap `events.raw` size or prune after 30 d.
- **Functions free 1M execs / 400k GB-s per mo** — single hourly timer ≈ 720 execs/mo. Each run does scrape + rss + dispatch in ~10–30 s. Well under both limits.
- **Single-timer run length** — Consumption plan caps execution at 5 min (configurable to 10). Plenty of headroom for current scope; revisit if a third source pushes total run-time near the cap.
- **Telegram 30 msg/s, 1/s per chat** — global `sleep(0.04)` + retry on `429 retry_after`.
- **Stripe** — test/live separated by env (`STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, distinct webhook signing secrets); idempotency via `stripe_events_seen`.
- **Service-role key** — only in Function App env + SWA env (consumed by Next.js API routes); RLS as second line of defense.
- **GDPR** — EU residency via Supabase Frankfurt; account-deletion cascades; publish privacy notice.
- **DiscoverEU API ToS** — gray-area internal endpoint; don't scale frequency.
- **Telegram webhook reliability** — if SWA is down when a user clicks the deep-link, Telegram retries briefly then drops the update; user just clicks again. Acceptable for a manual linking step. Webhook secured by `TELEGRAM_WEBHOOK_SECRET` header (Telegram's `setWebhook` `secret_token` param).
- **Cross-NGO duplicate events** — once a non-Czech adapter ships, the same Erasmus+ project will commonly be posted by multiple national sending NGOs. Dedup is deferred to Phase 4e (cluster, don't merge — see that sub-step for the canonical-key + name-similarity strategy). Schema reserves `events.erasmus_project_ref` and `events.cluster_id` so we don't paint into a corner before building.
- **Future: WhatsApp / second channel** — out of scope for MVP, but the schema (`profiles` could gain a `whatsapp_id` column, `subscriptions_filters` a `channel` column) and the dispatcher (channel-aware send) leave room. Don't build it now; just don't paint into a corner.

## Verification

- **Phase 1a**: local `func start` against a Supabase dev project, manually trigger `check_meetups` (`curl -X POST http://localhost:7071/admin/functions/check_meetups -H "Content-Type: application/json" -d '{}'`), confirm Supabase `events` row count matches `len(meetups_1)` AND the owner still receives the legacy Telegram message. Prod: deploy, wait one cycle, compare row counts; the owner alert must be byte-identical to the previous cycle.
- **Phase 1b**: switch the owner notification to be driven by Supabase newness (the ids returned from `upsert_events`). Trigger manually, confirm owner alert still fires for genuinely new meetups and is silent when none. Then delete `state.py` and the blob container.
- **Phase 2**: `npm run dev` against dev project, hit `/events` logged-out (RLS check). Deploy SWA preview branch first.
- **Phase 3**: create a **dev Telegram bot** via @BotFather; locally tunnel SWA via `swa start` + a public tunnel (e.g. `cloudflared`); call Bot API `setWebhook` pointing at `https://<tunnel>/api/telegram/webhook` with `secret_token`. Signup → click deep-link in browser → confirm `profiles.telegram_chat_id` populated → create filter → insert fake `events` row → confirm dispatcher sends message. Repeat in prod with a burner account.
- **Phase 4**: per-adapter, sample-fetch locally and run a single new item through `llm_extractor`; assert the returned dict passes the JSON schema + validation rules (`period_start ≤ period_end`, ISO-2 country, all required fields non-null). Deploy and confirm `events` rows appear with `source='youth_exchange'`. Verify a non-YE post (e.g. a Training Course on EYC's feed) returns `is_youth_exchange=false` and is correctly skipped. Verify dedup: a post already in `events` does not trigger a second LLM call on the next cycle. Force one adapter to throw (e.g. dead URL) and confirm other adapters still upsert normally — failure isolation. After 4c, confirm DiscoverEU output is byte-equivalent before/after the refactor.
- **Phase 5**: `stripe listen --forward-to localhost:3000/api/stripe/webhook` (Next.js dev server port) + test card `4242…`; verify `subscription_status` flips and the 2nd-filter trigger allows insert. Then live mode with a €1 sub immediately refunded.
- **Phase 6**: `/admin/health` smoke; force a scrape failure to confirm App Insights alert fires.

## Critical files

Modified / created in `backend/` (renamed from `discovereu-monitor/` in Phase 1a):
- `backend/function_app.py` (modified — Phase 1a: append shadow `upsert_events`; Phase 1b: drop blob path; Phase 3: rename to `hourly_run`, chain `dispatch_pending`; Phase 4: add `fetch_rss_feeds + upsert_events('youth_exchange')`)
- `backend/scraper.py` (untouched — keeps `fetch_meetups`, `get_all_meetups`, `compute_hash`)
- `backend/notifier.py` (untouched in Phase 1; Phase 3 adds `send_to_user(chat_id, msg)` alongside legacy `send_notification`)
- `backend/state.py` (untouched in Phase 1a; deleted in Phase 1b)
- `backend/supabase_client.py`, `backend/events_writer.py` (new in Phase 1a)
- `backend/dispatcher.py` (new in Phase 3)
- `backend/llm_extractor.py` — LLM extraction module (Phase 4a; Gemini 2.5 Flash-Lite, schema enforcement + validation; no source-specific knowledge)
- `backend/adapters/__init__.py`, `backend/adapters/<ngo>.py` — per-source adapter registry (Phase 4; one module per NGO with its own fetch + bespoke `EXTRACTION_PROMPT`, added incrementally)
- `backend/adapters/discovereu.py` — added in Phase 4c when DiscoverEU is folded into the registry (structured-wire-format adapter; bypasses `llm_extractor`)
- `backend/requirements.txt` (Phase 1a: `+ supabase`; Phase 4a: `+ google-generativeai`, `+ feedparser`; subsequent adapters: per-adapter fetch deps as needed, e.g. `lxml`, `httpx`, `icalendar`)

New under `web/`:
- `web/app/...` routes listed above
- `web/app/api/stripe/{checkout,portal,webhook}/route.ts` — Stripe endpoints as Next.js API routes
- `web/app/api/telegram/webhook/route.ts` — Telegram bot updates receiver
- `web/lib/supabase/{server,client}.ts`, `web/lib/stripe.ts`, `web/lib/telegram.ts`
- `web/staticwebapp.config.json`

New under `supabase/`:
- `supabase/migrations/0001_events.sql`
- `supabase/migrations/0002_profiles_filters_tokens_notifications.sql`
- `supabase/migrations/0003_rls_policies.sql`
- `supabase/migrations/0004_free_tier_trigger.sql`

CI:
- `.github/workflows/web-deploy.yml` (`paths: ['web/**']`)
- `.github/workflows/backend-deploy.yml` (`paths: ['backend/**']`)
