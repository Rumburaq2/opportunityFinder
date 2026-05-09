import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { createFilter } from "../actions";
import { FilterForm } from "../FilterForm";

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

      <p className="mt-2 text-sm text-zinc-500">
        You&apos;ll get a Telegram message when a matching event is published.
      </p>

      <FilterForm
        action={createFilter}
        submitLabel="Create filter"
        error={error}
      />
    </div>
  );
}
