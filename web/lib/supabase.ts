import "server-only";

import { createClient } from "@supabase/supabase-js";

export type EventSource = "discovereu" | "youth_exchange" | "training_course";

export type EventRow = {
  id: string;
  source: EventSource;
  name: string;
  description: string;
  period_start: string | null;
  period_end: string | null;
  country: string | null;
  partner_countries: string[] | null;
  url: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

let cached: ReturnType<typeof createClient> | null = null;

export function supabase() {
  if (cached) return cached;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anon) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY",
    );
  }
  cached = createClient(url, anon, { auth: { persistSession: false } });
  return cached;
}
