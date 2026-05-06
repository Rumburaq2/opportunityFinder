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

export default async function AccountPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: profile, error } = await supabase
    .from("profiles")
    .select(
      "id, subscription_status, telegram_chat_id, subscription_current_period_end",
    )
    .eq("id", user.id)
    .single<Profile>();

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

      {error && (
        <p className="mt-6 text-sm text-red-600 dark:text-red-400">
          Failed to load profile: {error.message}
        </p>
      )}

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
