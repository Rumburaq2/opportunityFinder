-- Phase 3a schema: user profile, subscription filters, telegram linking tokens, dedup ledger.
-- RLS policies live in 0003; the free-tier insert guard lives in 0004.

-- profiles: 1:1 with auth.users. Auto-created on signup via the trigger below.
create table if not exists public.profiles (
    id                              uuid primary key references auth.users(id) on delete cascade,
    telegram_chat_id                bigint unique,
    stripe_customer_id              text unique,
    subscription_status             text not null default 'none'
        check (subscription_status in ('none', 'active', 'past_due', 'canceled')),
    subscription_current_period_end timestamptz,
    created_at                      timestamptz not null default now()
);

-- Auto-create a profile row whenever a new auth.users row is inserted.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id) values (new.id);
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();


-- subscriptions_filters: a user's saved notification criteria. Free tier = 1 active row.
create table if not exists public.subscriptions_filters (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.profiles(id) on delete cascade,
    event_type  text not null default 'any'
        check (event_type in ('any', 'discovereu', 'youth_exchange')),
    country     text,
    date_from   date,
    date_to     date,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

create index if not exists subscriptions_filters_active_user_idx
    on public.subscriptions_filters (active, user_id);


-- telegram_link_tokens: short-lived nonce embedded in the bot deep-link.
-- Issued by the web app, consumed by the /api/telegram/webhook route via service-role.
create table if not exists public.telegram_link_tokens (
    token       text primary key,
    user_id     uuid not null references public.profiles(id) on delete cascade,
    expires_at  timestamptz not null default (now() + interval '15 minutes'),
    consumed_at timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists telegram_link_tokens_user_idx
    on public.telegram_link_tokens (user_id);


-- notifications_sent: dedup ledger written by the dispatcher after each successful send.
-- Composite PK is the dedup key; left-anti-join in the dispatcher excludes already-sent rows.
create table if not exists public.notifications_sent (
    user_id   uuid not null references public.profiles(id) on delete cascade,
    event_id  text not null references public.events(id) on delete cascade,
    filter_id uuid references public.subscriptions_filters(id) on delete set null,
    sent_at   timestamptz not null default now(),
    primary key (user_id, event_id)
);
