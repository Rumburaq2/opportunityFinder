-- Phase 4f-B: optional, per-filter eligibility ("only alert me for events I'm
-- eligible for, from my country").
--
-- Three additive schema changes + one function replacement. Everything defaults
-- to the *current* behavior, so existing users see zero change until they opt
-- in per filter:
--   * events.eligible_countries — the set of countries whose nationals can join
--     the event. NULL = declared-open (DiscoverEU only — never an extraction
--     outcome). National adapters fold in their sending country (CZ) so the set
--     is never empty; general adapters (SALTO) drop the event rather than store
--     an unknown set.
--   * profiles.home_country — the user's country, ISO-2. Collected lazily by the
--     web app (NOT at signup) the first time a user enables a filter's
--     eligibility toggle. Backfilled to 'CZ' since every current user is Czech.
--   * subscriptions_filters.eligible_only — the opt-in toggle, default false.
--
-- The pending_notifications() gate adds ONE clause: an event passes the
-- eligibility check when the filter hasn't opted in, OR the event is open
-- (NULL), OR the user's home country is in the eligible set.

alter table public.events
    add column if not exists eligible_countries text[];

create index if not exists events_eligible_countries_idx
    on public.events using gin (eligible_countries);

alter table public.profiles
    add column if not exists home_country text
        check (home_country is null or home_country ~ '^[A-Z]{2}$');

-- Every current user is Czech (Czech-only audience through Phase 4f-A).
update public.profiles
    set home_country = 'CZ'
    where home_country is null;

alter table public.subscriptions_filters
    add column if not exists eligible_only boolean not null default false;

-- Replace pending_notifications() (last defined in 0010) adding only the
-- eligibility clause to the existing events↔filters join. The profiles join
-- already exists, so p.home_country is in scope.
create or replace function public.pending_notifications()
returns table (
    user_id            uuid,
    telegram_chat_id   bigint,
    event_id           text,
    event_source       text,
    event_name         text,
    event_country      text,
    event_period_start date,
    event_period_end   date,
    event_url          text,
    filter_id          uuid
)
language sql
stable
as $$
    select
        p.id              as user_id,
        p.telegram_chat_id,
        e.id              as event_id,
        e.source          as event_source,
        e.name            as event_name,
        e.country         as event_country,
        e.period_start    as event_period_start,
        e.period_end      as event_period_end,
        e.url             as event_url,
        f.id              as filter_id
    from public.events e
    join public.subscriptions_filters f
        on f.active = true
       and (f.event_type = 'any' or f.event_type = e.source)
       and (f.country is null or f.country = e.country)
       and (f.date_from is null or e.period_start >= f.date_from)
       and (f.date_to   is null or e.period_start <= f.date_to)
       and e.first_seen_at >= f.created_at
    join public.profiles p
        on p.id = f.user_id
       and p.telegram_chat_id is not null
    where e.last_seen_at >= now() - interval '7 days'
      and (
          f.eligible_only = false
          or e.eligible_countries is null
          or p.home_country = any(e.eligible_countries)
      )
      and not exists (
          select 1
          from public.notifications_sent ns
          where ns.user_id = p.id
            and ns.event_id = e.id
      );
$$;
