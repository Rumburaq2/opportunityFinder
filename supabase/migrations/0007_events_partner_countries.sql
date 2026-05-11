-- Phase 4a: capture "participating countries" for Youth Exchange events.
--
-- For DiscoverEU meetups, country = host country = the only country involved.
-- For NGO Youth Exchanges, the post often lists additional participating
-- countries in narrative prose. The Phase 4a LLM extractor pulls those into
-- partner_countries (ISO-2, optional). DiscoverEU rows stay null.
--
-- Filter UX on partner_countries is intentionally deferred — this migration
-- only adds the column + index so the data starts accumulating.

alter table public.events
    add column if not exists partner_countries text[];

create index if not exists events_partner_countries_idx
    on public.events using gin (partner_countries);
