import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { createFilter } from "../actions";
import { FilterForm } from "../FilterForm";

type Profile = { subscription_status: string };
type Filter = { active: boolean };

export default async function NewFilterPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  // Mirror the /account page's canCreate gate so deep-linkers see the same
  // wall. The 0004 BEFORE-INSERT trigger remains the source of truth.
  const [{ data: profile }, { data: filters }] = await Promise.all([
    supabase
      .from("profiles")
      .select("subscription_status")
      .eq("id", user.id)
      .single<Profile>(),
    supabase
      .from("subscriptions_filters")
      .select("active")
      .eq("user_id", user.id)
      .returns<Filter[]>(),
  ]);

  const isPaid = profile?.subscription_status === "active";
  const activeCount = (filters ?? []).filter((f) => f.active).length;
  const canCreate = isPaid || activeCount === 0;

  const { error } = await searchParams;

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">New filter</h1>
        <Link
          href="/account"
          className="text-sm text-zinc-500 underline hover:no-underline"
        >
          Back to account
        </Link>
      </div>

      {canCreate ? (
        <>
          <p className="mt-2 text-sm text-zinc-500">
            You&apos;ll get a Telegram message when a matching event is
            published.
          </p>

          <FilterForm
            action={createFilter}
            submitLabel="Create filter"
            error={error}
          />
        </>
      ) : (
        <div className="mt-6 rounded-md border border-zinc-200 bg-zinc-50 p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="text-base font-semibold tracking-tight">
            Free-tier limit reached
          </h2>
          <p className="mt-2 text-sm text-zinc-500">
            Free accounts may have one active filter at a time. Upgrade to Pro
            for unlimited filters, or deactivate the existing one first.
          </p>
          <div className="mt-4 flex gap-3">
            <Link
              href="/account/billing"
              className="inline-flex h-9 items-center rounded-md bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              Upgrade — €5 / month
            </Link>
            <Link
              href="/account"
              className="inline-flex h-9 items-center rounded-md border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              Manage existing filter
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
