import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { deleteFilter, updateFilter } from "../actions";
import { FilterForm } from "../FilterForm";

type Filter = {
  id: string;
  event_type: "any" | "discovereu" | "youth_exchange" | "training_course";
  country: string | null;
  date_from: string | null;
  date_to: string | null;
  active: boolean;
  eligible_only: boolean;
};

type Profile = { home_country: string | null };

export default async function EditFilterPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [{ id }, { error: errorParam }] = await Promise.all([
    params,
    searchParams,
  ]);

  const [{ data: filter }, { data: profile }] = await Promise.all([
    supabase
      .from("subscriptions_filters")
      .select("id, event_type, country, date_from, date_to, active, eligible_only")
      .eq("id", id)
      .maybeSingle<Filter>(),
    supabase
      .from("profiles")
      .select("home_country")
      .eq("id", user.id)
      .single<Profile>(),
  ]);

  if (!filter) notFound();

  const update = updateFilter.bind(null, filter.id);
  const remove = deleteFilter.bind(null, filter.id);

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Edit filter</h1>
        <Link
          href="/account"
          className="text-sm text-zinc-500 underline hover:no-underline"
        >
          Back to account
        </Link>
      </div>

      <FilterForm
        action={update}
        homeCountry={profile?.home_country ?? null}
        submitLabel="Save changes"
        error={errorParam}
        initial={{
          event_type: filter.event_type,
          country: filter.country,
          date_from: filter.date_from,
          date_to: filter.date_to,
          active: filter.active,
          eligible_only: filter.eligible_only,
        }}
      />

      <form action={remove} className="mt-10 border-t border-zinc-200 pt-6 dark:border-zinc-800">
        <button
          type="submit"
          className="h-9 rounded-md border border-red-300 px-4 text-sm font-medium text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
        >
          Delete filter
        </button>
      </form>
    </div>
  );
}
