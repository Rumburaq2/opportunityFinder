-- Phase 5b: idempotency ledger for Stripe webhook events.
--
-- Stripe occasionally redelivers webhook events (network blips, our 200 ack
-- timing out, etc.). Inserting the event id with a primary-key conflict and
-- refusing to process duplicates keeps subscription-state writes idempotent.
--
-- The webhook handler inserts first; on `23505` it skips processing.

create table if not exists public.stripe_events_seen (
    event_id    text primary key,
    received_at timestamptz not null default now()
);

-- Service-role-only: only the Next.js webhook handler reads/writes this table.
-- No public surface; RLS on with no policies = deny all to anon/auth.
alter table public.stripe_events_seen enable row level security;
