import Link from "next/link";

import { supabase, type EventRow, type EventSource } from "@/lib/supabase";

const SOURCE_LABEL: Record<EventSource, string> = {
  discovereu: "DiscoverEU",
  youth_exchange: "Youth Exchange",
};

function formatDateRange(start: string | null, end: string | null) {
  if (!start && !end) return "Dates TBD";
  if (start && end && start === end) return start;
  return `${start ?? "?"} → ${end ?? "?"}`;
}

async function loadCountries(): Promise<string[]> {
  const { data, error } = await supabase()
    .from("events")
    .select("country")
    .not("country", "is", null)
    .order("country", { ascending: true });
  if (error) return [];
  const set = new Set<string>();
  for (const row of (data ?? []) as { country: string | null }[]) {
    if (row.country) set.add(row.country);
  }
  return [...set];
}

async function loadEvents(filters: {
  source: EventSource | null;
  country: string | null;
}): Promise<EventRow[]> {
  let query = supabase()
    .from("events")
    .select("*")
    .order("period_start", { ascending: true, nullsFirst: false })
    .limit(200);
  if (filters.source) query = query.eq("source", filters.source);
  if (filters.country) query = query.eq("country", filters.country);
  const { data, error } = await query;
  if (error) {
    console.error("loadEvents failed:", error);
    return [];
  }
  return (data ?? []) as EventRow[];
}

export default async function EventsPage({
  searchParams,
}: {
  searchParams: Promise<{ source?: string; country?: string }>;
}) {
  const params = await searchParams;
  const source: EventSource | null =
    params.source === "discovereu" || params.source === "youth_exchange"
      ? params.source
      : null;
  const country = params.country?.trim() || null;

  const [countries, events] = await Promise.all([
    loadCountries(),
    loadEvents({ source, country }),
  ]);

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-3xl font-semibold tracking-tight">Events</h1>
      <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
        {events.length} match{events.length === 1 ? "" : "es"}
      </p>

      <form
        method="get"
        className="mt-6 flex flex-wrap items-end gap-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-950"
      >
        <label className="flex flex-col text-sm">
          <span className="mb-1 text-zinc-600 dark:text-zinc-400">Source</span>
          <select
            name="source"
            defaultValue={source ?? ""}
            className="h-9 rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          >
            <option value="">All</option>
            <option value="discovereu">DiscoverEU</option>
            <option value="youth_exchange">Youth Exchange</option>
          </select>
        </label>

        <label className="flex flex-col text-sm">
          <span className="mb-1 text-zinc-600 dark:text-zinc-400">Country</span>
          <select
            name="country"
            defaultValue={country ?? ""}
            className="h-9 rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          >
            <option value="">All</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>

        <button
          type="submit"
          className="h-9 rounded-md bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Apply
        </button>
        {(source || country) && (
          <Link
            href="/events"
            className="text-sm text-zinc-600 underline hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Clear
          </Link>
        )}
      </form>

      {events.length === 0 ? (
        <p className="mt-12 text-center text-zinc-500">
          No events match these filters.
        </p>
      ) : (
        <ul className="mt-6 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950">
          {events.map((e) => (
            <li key={e.id}>
              <Link
                href={`/events/${encodeURIComponent(e.id)}`}
                className="block p-4 hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <div className="flex items-start justify-between gap-4">
                  <h2 className="font-medium">{e.name || "(untitled)"}</h2>
                  <span className="shrink-0 rounded-full border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
                    {SOURCE_LABEL[e.source]}
                  </span>
                </div>
                <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                  {formatDateRange(e.period_start, e.period_end)}
                  {e.country ? ` · ${e.country}` : ""}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
