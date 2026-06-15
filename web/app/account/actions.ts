"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { createClient } from "@/lib/supabase/server";
import { isKnownCountry } from "@/lib/countries";

export async function logout() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  revalidatePath("/", "layout");
  redirect("/");
}

// Edit the home country used by the optional per-filter eligibility gate
// (Phase 4f-B). Empty clears it (eligibility filters then match nothing until
// it's set again).
export async function updateHomeCountry(formData: FormData) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const raw = String(formData.get("home_country") ?? "")
    .trim()
    .toUpperCase();
  if (raw && !isKnownCountry(raw)) {
    redirect(
      `/account?error=${encodeURIComponent("Please pick a country from the list.")}`,
    );
  }

  const { error } = await supabase
    .from("profiles")
    .update({ home_country: raw || null })
    .eq("id", user.id);

  if (error) {
    redirect(`/account?error=${encodeURIComponent(error.message)}`);
  }

  revalidatePath("/account");
  redirect("/account?saved=1");
}
