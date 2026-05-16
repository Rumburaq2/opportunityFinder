"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { originFromHeaders } from "@/lib/origin";
import { createClient } from "@/lib/supabase/server";

export async function signup(formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");

  const supabase = await createClient();
  const h = await headers();
  // Behind SWA the request hits Next over an internal hop; the Origin header
  // is unreliable there. Prefer the forwarded-host pair; fall back to Origin
  // for local `next dev`.
  const origin = originFromHeaders(h) ?? h.get("origin") ?? "";

  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: `${origin}/auth/callback`,
    },
  });

  if (error) {
    redirect(`/signup?error=${encodeURIComponent(error.message)}`);
  }

  // If email confirmation is enabled in Supabase, the session is null until
  // the user clicks the link. Otherwise we have a session and go to /account.
  if (!data.session) {
    redirect("/signup?check_email=1");
  }

  revalidatePath("/", "layout");
  redirect("/account");
}
