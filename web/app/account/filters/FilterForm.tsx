"use client";

import { useState } from "react";

import { COUNTRIES } from "@/lib/countries";

type EventType = "any" | "discovereu" | "youth_exchange" | "training_course";

type FilterFormProps = {
  action: (formData: FormData) => void | Promise<void>;
  initial?: {
    event_type: EventType;
    host_countries: string[] | null;
    participant_countries: string[] | null;
    date_from: string | null;
    date_to: string | null;
    active: boolean;
    eligible_only: boolean;
  };
  /**
   * The user's saved home country (ISO-2), or null if never set. When the user
   * enables the eligibility toggle and this is null, the form reveals a required
   * country select so we can collect it lazily on save (Phase 4f-B).
   */
  homeCountry: string | null;
  submitLabel: string;
  error?: string;
};

export function FilterForm({
  action,
  initial,
  homeCountry,
  submitLabel,
  error,
}: FilterFormProps) {
  const [eventType, setEventType] = useState(initial?.event_type ?? "any");
  const [eligibleOnly, setEligibleOnly] = useState(
    initial?.eligible_only ?? false,
  );

  // Eligibility is meaningless for DiscoverEU (open to all), so the whole block
  // is hidden there. The country select only appears when the user opts in and
  // we don't already know their home country.
  const showEligibility = eventType !== "discovereu";
  const needsHomeCountry = eligibleOnly && !homeCountry;

  return (
    <form action={action} className="mt-6 space-y-5">
      <div>
        <label
          htmlFor="event_type"
          className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
        >
          Event type
        </label>
        <select
          id="event_type"
          name="event_type"
          value={eventType}
          onChange={(e) => setEventType(e.target.value as EventType)}
          className="mt-1 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
        >
          <option value="any">Any</option>
          <option value="discovereu">DiscoverEU only</option>
          <option value="youth_exchange">Youth exchange only</option>
          <option value="training_course">Training course only</option>
        </select>
      </div>

      <div>
        <label
          htmlFor="host_countries"
          className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
        >
          Host country (optional)
        </label>
        <select
          id="host_countries"
          name="host_countries"
          multiple
          size={6}
          defaultValue={initial?.host_countries ?? []}
          className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
        >
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-500">
          Where the event is held. Select one or more — an event in{" "}
          <em>any</em> of them matches. Leave empty for any country.
        </p>
      </div>

      <div>
        <label
          htmlFor="participant_countries"
          className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
        >
          Participating countries (optional)
        </label>
        <select
          id="participant_countries"
          name="participant_countries"
          multiple
          size={6}
          defaultValue={initial?.participant_countries ?? []}
          className="mt-1 w-full rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
        >
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-500">
          Only notify me when <em>all</em> selected countries take part (host or
          partner). Leave empty to ignore participants.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="date_from"
            className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
          >
            From (optional)
          </label>
          <input
            id="date_from"
            name="date_from"
            type="date"
            defaultValue={initial?.date_from ?? ""}
            className="mt-1 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
          />
        </div>
        <div>
          <label
            htmlFor="date_to"
            className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
          >
            To (optional)
          </label>
          <input
            id="date_to"
            name="date_to"
            type="date"
            defaultValue={initial?.date_to ?? ""}
            className="mt-1 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
          />
        </div>
      </div>

      {showEligibility && (
        <div className="space-y-3 rounded-md border border-zinc-200 p-4 dark:border-zinc-800">
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              name="eligible_only"
              checked={eligibleOnly}
              onChange={(e) => setEligibleOnly(e.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>
              Only notify me for events I&apos;m eligible for (from my country)
              <span className="mt-0.5 block text-xs text-zinc-500">
                Skips events that only accept participants from other countries.
                Leave off to see everything (e.g. searching on behalf of a friend
                abroad).
              </span>
            </span>
          </label>

          {needsHomeCountry && (
            <div>
              <label
                htmlFor="home_country"
                className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Your country
              </label>
              <select
                id="home_country"
                name="home_country"
                required
                defaultValue=""
                className="mt-1 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
              >
                <option value="" disabled>
                  Select your country…
                </option>
                {COUNTRIES.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-zinc-500">
                Saved to your profile. You can change it later in your account.
              </p>
            </div>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          name="active"
          defaultChecked={initial?.active ?? true}
          className="h-4 w-4"
        />
        <span>Active (counts toward free-tier limit when checked)</span>
      </label>

      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      <button
        type="submit"
        className="h-9 rounded-md bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        {submitLabel}
      </button>
    </form>
  );
}
