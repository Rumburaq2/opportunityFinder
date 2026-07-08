"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { createClient } from "@/lib/supabase/server";
import { isKnownCountry } from "@/lib/countries";

const EVENT_TYPES = [
  "any",
  "discovereu",
  "youth_exchange",
  "training_course",
] as const;
type EventType = (typeof EVENT_TYPES)[number];

type FilterInput = {
  event_type: EventType;
  country: string | null;
  host_countries: string[] | null;
  participant_countries: string[] | null;
  date_from: string | null;
  date_to: string | null;
  active: boolean;
  eligible_only: boolean;
};

/**
 * Parse a multi-country form field (submitted as repeated values by a
 * <select multiple>) into a deduped, uppercased ISO-2 array. Returns null when
 * empty (= no constraint) or a validation error string on an unknown code.
 */
function parseCountryList(
  values: FormDataEntryValue[],
): string[] | { error: string } | null {
  const codes = Array.from(
    new Set(
      values
        .map((v) => String(v).trim().toUpperCase())
        .filter((v) => v.length > 0),
    ),
  );
  if (codes.length === 0) return null;
  for (const code of codes) {
    if (!isKnownCountry(code)) {
      return { error: "Please pick countries from the list." };
    }
  }
  return codes;
}

type ParsedForm = {
  filter: FilterInput;
  // Home country submitted with the form (lazy collection — Phase 4f-B), or
  // null when the field wasn't shown. Persisted to the profile, not the filter.
  homeCountry: string | null;
};

function parseForm(formData: FormData): ParsedForm | { error: string } {
  const rawEventType = String(formData.get("event_type") ?? "any");
  if (!EVENT_TYPES.includes(rawEventType as EventType)) {
    return { error: "Invalid event type." };
  }

  const country = String(formData.get("country") ?? "")
    .trim()
    .toUpperCase();
  if (country && !/^[A-Z]{2}$/.test(country)) {
    return { error: "Country must be a 2-letter code (e.g. DE, FR)." };
  }

  const hostCountries = parseCountryList(formData.getAll("host_countries"));
  if (hostCountries && "error" in hostCountries) return hostCountries;
  const participantCountries = parseCountryList(
    formData.getAll("participant_countries"),
  );
  if (participantCountries && "error" in participantCountries) {
    return participantCountries;
  }

  const dateFrom = String(formData.get("date_from") ?? "").trim() || null;
  const dateTo = String(formData.get("date_to") ?? "").trim() || null;
  if (dateFrom && dateTo && dateFrom > dateTo) {
    return { error: "End date must be on or after start date." };
  }

  // Eligibility is hidden for DiscoverEU, so the checkbox simply won't be
  // present then and this resolves to false.
  const eligibleOnly =
    rawEventType !== "discovereu" && formData.get("eligible_only") === "on";

  const rawHome = String(formData.get("home_country") ?? "")
    .trim()
    .toUpperCase();
  if (rawHome && !isKnownCountry(rawHome)) {
    return { error: "Please pick your country from the list." };
  }

  return {
    filter: {
      event_type: rawEventType as EventType,
      country: country || null,
      host_countries: hostCountries,
      participant_countries: participantCountries,
      date_from: dateFrom,
      date_to: dateTo,
      active: formData.get("active") === "on",
      eligible_only: eligibleOnly,
    },
    homeCountry: rawHome || null,
  };
}

function friendlyDbError(message: string): string {
  if (message.includes("free_tier_filter_limit")) {
    return "Free accounts may have at most one active filter. Deactivate or delete the existing one, or upgrade.";
  }
  return message;
}

/**
 * Lazily persist the user's home country when they first enable eligibility.
 * Returns an error string if the toggle is on but we have no country (neither
 * submitted now nor saved earlier), otherwise null on success.
 */
async function ensureHomeCountry(
  supabase: Awaited<ReturnType<typeof createClient>>,
  userId: string,
  parsed: ParsedForm,
): Promise<string | null> {
  if (!parsed.filter.eligible_only) return null;

  if (parsed.homeCountry) {
    const { error } = await supabase
      .from("profiles")
      .update({ home_country: parsed.homeCountry })
      .eq("id", userId);
    return error ? error.message : null;
  }

  // No country submitted — only valid if the profile already has one.
  const { data } = await supabase
    .from("profiles")
    .select("home_country")
    .eq("id", userId)
    .single<{ home_country: string | null }>();
  if (!data?.home_country) {
    return "Please choose your country to enable eligibility filtering.";
  }
  return null;
}

export async function createFilter(formData: FormData) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const parsed = parseForm(formData);
  if ("error" in parsed) {
    redirect(`/account/filters/new?error=${encodeURIComponent(parsed.error)}`);
  }

  const homeError = await ensureHomeCountry(supabase, user.id, parsed);
  if (homeError) {
    redirect(`/account/filters/new?error=${encodeURIComponent(homeError)}`);
  }

  const { error } = await supabase
    .from("subscriptions_filters")
    .insert({ ...parsed.filter, user_id: user.id });

  if (error) {
    redirect(
      `/account/filters/new?error=${encodeURIComponent(friendlyDbError(error.message))}`,
    );
  }

  revalidatePath("/account");
  redirect("/account");
}

export async function updateFilter(id: string, formData: FormData) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const parsed = parseForm(formData);
  if ("error" in parsed) {
    redirect(
      `/account/filters/${id}?error=${encodeURIComponent(parsed.error)}`,
    );
  }

  const homeError = await ensureHomeCountry(supabase, user.id, parsed);
  if (homeError) {
    redirect(`/account/filters/${id}?error=${encodeURIComponent(homeError)}`);
  }

  const { error } = await supabase
    .from("subscriptions_filters")
    .update(parsed.filter)
    .eq("id", id);

  if (error) {
    redirect(
      `/account/filters/${id}?error=${encodeURIComponent(friendlyDbError(error.message))}`,
    );
  }

  revalidatePath("/account");
  redirect("/account");
}

export async function deleteFilter(id: string) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { error } = await supabase
    .from("subscriptions_filters")
    .delete()
    .eq("id", id);

  if (error) {
    throw new Error(`Failed to delete filter: ${error.message}`);
  }

  revalidatePath("/account");
  redirect("/account");
}
