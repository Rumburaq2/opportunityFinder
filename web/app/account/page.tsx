import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { logout } from "./actions";

type Profile = {
  id: string;
  subscription_status: "none" | "active" | "past_due" | "canceled";
  telegram_chat_id: number | null;
  subscription_current_period_end: string | null;
};

type Filter = {
  id: string;
  event_type: "any" | "discovereu" | "youth_exchange";
  country: string | null;
  date_from: string | null;
  date_to: string | null;
  active: boolean;
};

const EVENT_TYPE_LABEL: Record<Filter["event_type"], string> = {
  any: "Any",
  discovereu: "DiscoverEU",
  youth_exchange: "Youth exchange",
};

function describeFilter(f: Filter): string {
  const parts: string[] = [EVENT_TYPE_LABEL[f.event_type]];
  if (f.country) parts.push(f.country);
  if (f.date_from || f.date_to) {
    const from = f.date_from ?? "…";
    const to = f.date_to ?? "…";
    parts.push(`${from} → ${to}`);
  }
  return parts.join(" · ");
}

export default async function AccountPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [{ data: profile, error: profileError }, { data: filters }] =
    await Promise.all([
      supabase
        .from("profiles")
        .select(
          "id, subscription_status, telegram_chat_id, subscription_current_period_end",
        )
        .eq("id", user.id)
        .single<Profile>(),
      supabase
        .from("subscriptions_filters")
        .select("id, event_type, country, date_from, date_to, active")
        .eq("user_id", user.id)
        .order("created_at", { ascending: false })
        .returns<Filter[]>(),
    ]);

  const isPaid = profile?.subscription_status === "active";
  const activeCount = (filters ?? []).filter((f) => f.active).length;
  const canCreate = isPaid || activeCount === 0 || (filters ?? []).length === 0;

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Your account</h1>

      <dl className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-[max-content_1fr] sm:gap-x-8">
        <dt className="text-sm text-zinc-500">Email</dt>
        <dd className="text-sm">{user.email}</dd>

        <dt className="text-sm text-zinc-500">Subscription</dt>
        <dd className="text-sm">
          {profile?.subscription_status ?? "—"}
          {profile?.subscription_current_period_end
            ? ` (until ${profile.subscription_current_period_end.slice(0, 10)})`
            : ""}
        </dd>

        <dt className="text-sm text-zinc-500">Telegram linked</dt>
        <dd className="text-sm">
          {profile?.telegram_chat_id ? (
            "Yes"
          ) : (
            <Link
              href="/account/link-telegram"
              className="underline hover:no-underline"
            >
              Link now
            </Link>
          )}
        </dd>
      </dl>

      {profileError && (
        <p className="mt-6 text-sm text-red-600 dark:text-red-400">
          Failed to load profile: {profileError.message}
        </p>
      )}

      <section className="mt-12">
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold tracking-tight">Filters</h2>
          {canCreate ? (
            <Link
              href="/account/filters/new"
              className="text-sm underline hover:no-underline"
            >
              + New filter
            </Link>
          ) : (
            <span
              className="text-sm text-zinc-500"
              title="Free accounts may have one active filter. Deactivate the existing one or upgrade."
            >
              Free-tier limit of filters reached
            </span>
          )}
        </div>

        {!filters || filters.length === 0 ? (
          <p className="mt-4 text-sm text-zinc-500">
            You don&apos;t have any filters yet. Create one to start receiving
            Telegram alerts.
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-zinc-200 rounded-md border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {filters.map((f) => (
              <li
                key={f.id}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm">{describeFilter(f)}</p>
                  <p className="text-xs text-zinc-500">
                    {f.active ? "Active" : "Paused"}
                  </p>
                </div>
                <Link
                  href={`/account/filters/${f.id}`}
                  className="text-sm underline hover:no-underline"
                >
                  Edit
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form action={logout} className="mt-10">
        <button
          type="submit"
          className="h-9 rounded-md border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          Log out
        </button>
      </form>
    </div>
  );
}
