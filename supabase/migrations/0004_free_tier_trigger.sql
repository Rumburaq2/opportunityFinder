-- Phase 3a free-tier guard.
-- Free users may have at most 1 active filter. Paid users (subscription_status='active')
-- are unrestricted. Inactive filters never count and are always allowed.
-- This is a DB-side backstop; the Next.js UI also gates filter creation, but this
-- trigger is the source of truth so the limit cannot be bypassed via the API.

create or replace function public.enforce_free_tier_filter_limit()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    is_paid       boolean;
    active_count  integer;
begin
    -- Only constrain rows that count toward the limit.
    if new.active is not true then
        return new;
    end if;

    select subscription_status = 'active'
      into is_paid
      from public.profiles
     where id = new.user_id;

    if is_paid then
        return new;
    end if;

    select count(*)
      into active_count
      from public.subscriptions_filters
     where user_id = new.user_id
       and active is true
       and id is distinct from new.id;  -- ignore self on UPDATE

    if active_count >= 1 then
        raise exception 'free_tier_filter_limit'
            using hint = 'Free users may have at most 1 active filter. Upgrade or deactivate the existing one.';
    end if;

    return new;
end;
$$;

drop trigger if exists subscriptions_filters_free_tier_guard on public.subscriptions_filters;
create trigger subscriptions_filters_free_tier_guard
    before insert or update on public.subscriptions_filters
    for each row execute function public.enforce_free_tier_filter_limit();
