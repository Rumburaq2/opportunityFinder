"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { createClient } from "@/lib/supabase/server";

const EVENT_TYPES = ["any", "discovereu", "youth_exchange"] as const;
type EventType = (typeof EVENT_TYPES)[number];

type FilterInput = {
  event_type: EventType;
  country: string | null;
  date_from: string | null;
  date_to: string | null;
  active: boolean;
};

function parseForm(formData: FormData): FilterInput | { error: string } {
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

  const dateFrom = String(formData.get("date_from") ?? "").trim() || null;
  const dateTo = String(formData.get("date_to") ?? "").trim() || null;
  if (dateFrom && dateTo && dateFrom > dateTo) {
    return { error: "End date must be on or after start date." };
  }

  return {
    event_type: rawEventType as EventType,
    country: country || null,
    date_from: dateFrom,
    date_to: dateTo,
    active: formData.get("active") === "on",
  };
}

function friendlyDbError(message: string): string {
  if (message.includes("free_tier_filter_limit")) {
    return "Free accounts may have at most one active filter. Deactivate or delete the existing one, or upgrade.";
  }
  return message;
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

  const { error } = await supabase
    .from("subscriptions_filters")
    .insert({ ...parsed, user_id: user.id });

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

  const { error } = await supabase
    .from("subscriptions_filters")
    .update(parsed)
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
