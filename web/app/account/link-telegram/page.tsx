import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { issueLinkToken } from "./actions";

type LinkToken = {
  token: string;
  expires_at: string;
  consumed_at: string | null;
};

type Profile = {
  telegram_chat_id: number | null;
};

export default async function LinkTelegramPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [{ data: profile }, { data: tokens }] = await Promise.all([
    supabase
      .from("profiles")
      .select("telegram_chat_id")
      .eq("id", user.id)
      .single<Profile>(),
    supabase
      .from("telegram_link_tokens")
      .select("token, expires_at, consumed_at")
      .eq("user_id", user.id)
      .is("consumed_at", null)
      .gt("expires_at", new Date().toISOString())
      .order("expires_at", { ascending: false })
      .limit(1),
  ]);

  const activeToken = (tokens?.[0] ?? null) as LinkToken | null;
  const botUsername = process.env.TELEGRAM_BOT_USERNAME;
  const deepLink =
    activeToken && botUsername
      ? `https://t.me/${botUsername}?start=${activeToken.token}`
      : null;

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Link Telegram</h1>

      {profile?.telegram_chat_id ? (
        <p className="mt-6 text-sm">
          Telegram is already linked to this account. Send <code>/stop</code> to
          the bot to unlink.
        </p>
      ) : !botUsername ? (
        <p className="mt-6 text-sm text-red-600 dark:text-red-400">
          Bot is not configured (missing TELEGRAM_BOT_USERNAME). Contact the
          admin.
        </p>
      ) : deepLink && activeToken ? (
        <div className="mt-6 space-y-4 text-sm">
          <p>Click the button to open Telegram and confirm the link.</p>
          <a
            href={deepLink}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block h-9 rounded-md bg-zinc-900 px-4 leading-9 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Open in Telegram
          </a>
          <p className="text-xs text-zinc-500">
            This link expires{" "}
            {new Date(activeToken.expires_at).toLocaleString()}. After
            confirming, return to{" "}
            <Link href="/account" className="underline">
              your account
            </Link>
            .
          </p>
        </div>
      ) : (
        <form action={issueLinkToken} className="mt-6">
          <button
            type="submit"
            className="h-9 rounded-md bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Generate link
          </button>
          <p className="mt-2 text-xs text-zinc-500">
            We&apos;ll generate a one-time link valid for 15 minutes.
          </p>
        </form>
      )}
    </div>
  );
}
