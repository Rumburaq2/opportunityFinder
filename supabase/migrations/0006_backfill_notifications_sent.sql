-- Phase 3d: pre-seed notifications_sent with every (linked-user, existing-event)
-- pair so the dispatcher's first run doesn't flood users with a backlog.
--
-- The dispatcher's pending_notifications() function returns events whose
-- last_seen_at is within the last 7 days, joined to active filters. Without
-- this backfill, deploying the dispatcher would immediately match the owner
-- (and any other linked user) against ~hundreds of historical events.
--
-- After applying this migration, pending_notifications() should return zero
-- rows — confirming a clean cutover. Future cycles only see genuinely new
-- events (whose last_seen_at gets refreshed on each upsert) that the user's
-- filter matches and that aren't already in notifications_sent.
--
-- One-shot: apply once, before deploying the dispatcher code. Idempotent via
-- ON CONFLICT (re-running is a no-op).

insert into public.notifications_sent (user_id, event_id, filter_id)
select
    p.id    as user_id,
    e.id    as event_id,
    null    as filter_id  -- backfill is filter-agnostic; future inserts attribute the matching filter
from public.events e
cross join public.profiles p
where p.telegram_chat_id is not null
on conflict (user_id, event_id) do nothing;
