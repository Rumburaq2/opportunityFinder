-- Multi-country host + participant filtering.
--
-- Lets a single filter express "host is ANY of these countries" and "the
-- participating set includes ALL of these countries" — e.g. a youth exchange
-- hosted in North Macedonia OR Serbia where Czechia AND Türkiye both take part.
--
-- Two additive columns + one function replacement. Both columns default NULL
-- (= no constraint), so every existing filter and the dispatcher behave exactly
-- as before until a user opts in. The legacy single-value `country` column is
-- kept for back-compat; the web UI now writes the array columns instead and
-- leaves `country` NULL. The RPC null-guards all three, so old rows (country
-- set, arrays NULL) and new rows (arrays set, country NULL) both match cleanly.
--
--   * subscriptions_filters.host_countries — host country must be ANY of these
--     (OR). NULL = any host.
--   * subscriptions_filters.participant_countries — the event's participant set
--     (host country + partner_countries) must include ALL of these (contains-
--     all). NULL = no participant constraint. An event whose partner_countries
--     is NULL has participant set {host} only, so a non-trivial requirement
--     won't match it — the event is dropped rather than sent on a maybe. This
--     matches the Phase 4f-B "accept event loss over flooding" decision.

alter table public.subscriptions_filters
    add column if not exists host_countries        text[],
    add column if not exists participant_countries text[];

-- Replace pending_notifications() (last defined in 0012) adding only the two
-- new host/participant clauses to the existing events↔filters join. The
-- filter-age gate, eligibility clause, and already-sent guard are unchanged.
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
       and (f.host_countries is null or e.country = any(f.host_countries))
       and (f.participant_countries is null
            or f.participant_countries
               <@ (array[e.country] || coalesce(e.partner_countries, '{}')))
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
