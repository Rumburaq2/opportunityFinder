-- Phase 4a follow-up: ledger of source rows we deliberately discarded.
--
-- Without this, the EYC adapter re-extracts every non-Youth-Exchange post on
-- the feed (Training Courses, ESC placements, Youth Worker Mobility) on every
-- hourly cycle — ~120 wasted Gemini calls/day per adapter. Scales linearly as
-- more adapters land.
--
-- The pre-dedup check in each adapter now consults events ∪ skipped_sources,
-- so a post is processed at most once. We only record decisions that are
-- non-retryable (the post will never become a YE; or its dates have passed);
-- transient failures (extraction validation, PDF fetch) are intentionally
-- NOT recorded so they retry next cycle.

create table if not exists public.skipped_sources (
    source_id   text primary key,
    adapter     text not null,
    reason      text not null,
    seen_at     timestamptz not null default now()
);

create index if not exists skipped_sources_adapter_idx
    on public.skipped_sources (adapter);

-- Service-role-only: backend writes, no public reads needed.
alter table public.skipped_sources enable row level security;
