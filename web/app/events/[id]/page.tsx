import Link from "next/link";
import { notFound } from "next/navigation";

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

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { data, error } = await supabase()
    .from("events")
    .select("*")
    .eq("id", decodeURIComponent(id))
    .maybeSingle();
  if (error) {
    console.error("event detail load failed:", error);
    notFound();
  }
  if (!data) notFound();
  const event = data as EventRow;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Link
        href="/events"
        className="text-sm text-zinc-600 hover:underline dark:text-zinc-400"
      >
        ← Back to events
      </Link>
      <div className="mt-4 flex items-start justify-between gap-4">
        <h1 className="text-3xl font-semibold tracking-tight">
          {event.name || "(untitled)"}
        </h1>
        <span className="shrink-0 rounded-full border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          {SOURCE_LABEL[event.source]}
        </span>
      </div>
      <div className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
        {formatDateRange(event.period_start, event.period_end)}
        {event.country ? ` · ${event.country}` : ""}
      </div>
      {event.partner_countries && event.partner_countries.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
          <span className="text-zinc-500">Partner countries:</span>
          {event.partner_countries.map((c) => (
            <span
              key={c}
              className="rounded-full border border-zinc-200 px-2 py-0.5 dark:border-zinc-700"
            >
              {c}
            </span>
          ))}
        </div>
      )}
      {event.description && (
        <p className="mt-6 whitespace-pre-line text-zinc-800 dark:text-zinc-200">
          {event.description}
        </p>
      )}
      {event.url && (
        <div className="mt-8">
          <a
            href={event.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-10 items-center rounded-full bg-zinc-900 px-5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Open original →
          </a>
        </div>
      )}
    </div>
  );
}
