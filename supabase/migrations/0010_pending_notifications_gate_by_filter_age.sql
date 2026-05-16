-- Gate pending_notifications() by filter age so a freshly-created filter only
-- fires on events that appeared *after* the filter was created.
--
-- Without this, a brand-new user who creates an ANY filter immediately matches
-- every event seen in the last 7 days and gets spammed with the backlog the
-- first time the dispatcher runs (observed 2026-05-16). Per-filter gating is
-- preferable to per-user because a user adding a second filter later expects
-- that new filter to fire only from then on, not from their original signup.
--
-- Only one clause changes vs. 0005: `and e.first_seen_at >= f.created_at`.
-- Idempotent: re-running this migration just replaces the function definition.

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
      and not exists (
          select 1
          from public.notifications_sent ns
          where ns.user_id = p.id
            and ns.event_id = e.id
      );
$$;
