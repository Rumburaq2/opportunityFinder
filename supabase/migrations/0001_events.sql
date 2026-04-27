-- Events table: union of DiscoverEU meetups (Phase 1a) and NGO youth-exchange RSS items (Phase 4).
-- The id is namespaced by source so the two ingestion paths never collide:
--   discovereu:<eu-id>          (Phase 1a)
--   ngo:<feed-slug>:<rss-guid>  (Phase 4)

create table if not exists public.events (
    id              text primary key,
    source          text not null check (source in ('discovereu', 'youth_exchange')),
    name            text not null default '',
    description     text not null default '',
    period_start    date,
    period_end      date,
    country         text,
    url             text,
    raw             jsonb,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now()
);

create index if not exists events_source_idx          on public.events (source);
create index if not exists events_country_idx         on public.events (country);
create index if not exists events_period_start_idx    on public.events (period_start);
create index if not exists events_source_country_ps_idx on public.events (source, country, period_start);

-- RLS: public read, no client writes. The Function App uses the service-role key
-- which bypasses RLS, so writes still work from the backend.
alter table public.events enable row level security;

drop policy if exists "events are publicly readable" on public.events;
create policy "events are publicly readable"
    on public.events
    for select
    to anon, authenticated
    using (true);
