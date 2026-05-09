-- Phase 3d: dispatcher's match query, exposed as a SQL function so the
-- backend can RPC it without round-tripping multiple PostgREST calls.
--
-- Returns one row per (user, event, filter) match that hasn't been sent yet.
-- The dispatcher iterates the result, sends each via Telegram, and inserts
-- into notifications_sent on success.
--
-- Service role bypasses RLS, so this function does not need security definer.
-- Date matching uses period_start (events with a null period_start won't
-- match a date-bounded filter — acceptable; null-date events are rare junk).

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
