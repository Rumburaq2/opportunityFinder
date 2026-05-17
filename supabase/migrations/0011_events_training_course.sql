-- Phase 4c: add training_course as a sibling source to youth_exchange.
-- The NGO adapters now classify each post as YE or TC (instead of dropping TC),
-- so both the events table and the filter dropdown need to accept the new value.
--
-- pending_notifications() needs no change: its match clause is
-- `f.event_type = 'any' or f.event_type = e.source`, which generalizes to any
-- new source value automatically. Note this means filters with
-- event_type='any' will start matching training_course rows as soon as the
-- backend ships — that's the intended behavior.

alter table public.events
    drop constraint if exists events_source_check;
alter table public.events
    add constraint events_source_check
    check (source in ('discovereu', 'youth_exchange', 'training_course'));

alter table public.subscriptions_filters
    drop constraint if exists subscriptions_filters_event_type_check;
alter table public.subscriptions_filters
    add constraint subscriptions_filters_event_type_check
    check (event_type in ('any', 'discovereu', 'youth_exchange', 'training_course'));
