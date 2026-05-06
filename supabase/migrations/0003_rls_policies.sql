-- Phase 3a RLS policies. Service-role bypasses RLS, so the Function App and
-- the Next.js API routes (which use the service-role key) are unaffected.
-- Events policies are already in 0001_events.sql.

-- profiles: owner can read + update their own row. No insert (handled by trigger).
-- No delete from clients (cascades from auth.users deletion).
alter table public.profiles enable row level security;

drop policy if exists "profiles owner select" on public.profiles;
create policy "profiles owner select"
    on public.profiles
    for select
    to authenticated
    using (auth.uid() = id);

drop policy if exists "profiles owner update" on public.profiles;
create policy "profiles owner update"
    on public.profiles
    for update
    to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);


-- subscriptions_filters: owner full CRUD, scoped to their own rows.
alter table public.subscriptions_filters enable row level security;

drop policy if exists "filters owner select" on public.subscriptions_filters;
create policy "filters owner select"
    on public.subscriptions_filters
    for select
    to authenticated
    using (auth.uid() = user_id);

drop policy if exists "filters owner insert" on public.subscriptions_filters;
create policy "filters owner insert"
    on public.subscriptions_filters
    for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "filters owner update" on public.subscriptions_filters;
create policy "filters owner update"
    on public.subscriptions_filters
    for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "filters owner delete" on public.subscriptions_filters;
create policy "filters owner delete"
    on public.subscriptions_filters
    for delete
    to authenticated
    using (auth.uid() = user_id);


-- telegram_link_tokens: owner can issue + read their own tokens.
-- Consumption (update consumed_at, lookup by token) happens via the service-role
-- key inside /api/telegram/webhook, which bypasses RLS.
alter table public.telegram_link_tokens enable row level security;

drop policy if exists "telegram tokens owner insert" on public.telegram_link_tokens;
create policy "telegram tokens owner insert"
    on public.telegram_link_tokens
    for insert
    to authenticated
    with check (auth.uid() = user_id);

drop policy if exists "telegram tokens owner select" on public.telegram_link_tokens;
create policy "telegram tokens owner select"
    on public.telegram_link_tokens
    for select
    to authenticated
    using (auth.uid() = user_id);


-- notifications_sent: owner can read their delivery history. No client writes.
-- The dispatcher inserts via service-role.
alter table public.notifications_sent enable row level security;

drop policy if exists "notifications_sent owner select" on public.notifications_sent;
create policy "notifications_sent owner select"
    on public.notifications_sent
    for select
    to authenticated
    using (auth.uid() = user_id);
