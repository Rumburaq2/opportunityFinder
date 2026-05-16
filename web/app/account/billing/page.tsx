import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

type Profile = {
  subscription_status: "none" | "active" | "past_due" | "canceled";
  subscription_current_period_end: string | null;
  stripe_customer_id: string | null;
};

type SearchParams = {
  checkout?: string;
  portal?: string;
};

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select(
      "subscription_status, subscription_current_period_end, stripe_customer_id",
    )
    .eq("id", user.id)
    .single<Profile>();

  const status = profile?.subscription_status ?? "none";
  const isPaid = status === "active";
  const params = await searchParams;

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Billing</h1>

      {params.checkout === "success" && (
        <div className="mt-6 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm dark:border-emerald-900 dark:bg-emerald-950">
          Thanks! Your subscription is being activated. It may take a moment to
          appear here.
        </div>
      )}
      {params.checkout === "cancel" && (
        <div className="mt-6 rounded-md border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
          Checkout cancelled. No charge was made.
        </div>
      )}
      {params.portal === "no-customer" && (
        <div className="mt-6 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm dark:border-amber-900 dark:bg-amber-950">
          You don&apos;t have a billing portal yet — subscribe first.
        </div>
      )}

      <dl className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-[max-content_1fr] sm:gap-x-8">
        <dt className="text-sm text-zinc-500">Plan</dt>
        <dd className="text-sm">{isPaid ? "Pro (€5 / month)" : "Free"}</dd>

        <dt className="text-sm text-zinc-500">Status</dt>
        <dd className="text-sm">{status}</dd>

        {profile?.subscription_current_period_end && (
          <>
            <dt className="text-sm text-zinc-500">Renews / ends</dt>
            <dd className="text-sm">
              {profile.subscription_current_period_end.slice(0, 10)}
            </dd>
          </>
        )}
      </dl>

      <div className="mt-8">
        {isPaid ? (
          <form action="/api/stripe/portal" method="post">
            <button
              type="submit"
              className="h-9 rounded-md border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              Manage subscription
            </button>
          </form>
        ) : (
          <form action="/api/stripe/checkout" method="post">
            <button
              type="submit"
              className="h-9 rounded-md bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              Upgrade — €5 / month
            </button>
          </form>
        )}
      </div>

      <p className="mt-6 text-sm text-zinc-500">
        Pro unlocks unlimited filters. Cancel anytime — access continues until
        the end of the billing period.
      </p>
    </div>
  );
}
