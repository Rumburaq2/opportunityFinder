"use client";

import { useState } from "react";
import { useFormStatus } from "react-dom";

import { COUNTRIES } from "@/lib/countries";

function SaveButton() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="h-9 rounded-md border border-zinc-300 px-3 text-sm font-medium hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
    >
      {pending ? "Saving…" : "Save"}
    </button>
  );
}

export function HomeCountryForm({
  action,
  current,
  saved,
}: {
  action: (formData: FormData) => void | Promise<void>;
  current: string | null;
  /** True right after a successful save (from the ?saved redirect param). */
  saved: boolean;
}) {
  // Hide the "Saved" confirmation as soon as the user edits the selection again,
  // so it never lingers next to an unsaved change.
  const [dirty, setDirty] = useState(false);

  return (
    <form action={action} className="flex flex-wrap items-center gap-2">
      <select
        name="home_country"
        defaultValue={current ?? ""}
        onChange={() => setDirty(true)}
        className="h-9 rounded-md border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950"
      >
        <option value="">Not set</option>
        {COUNTRIES.map((c) => (
          <option key={c.code} value={c.code}>
            {c.name}
          </option>
        ))}
      </select>
      <SaveButton />
      {saved && !dirty && (
        <span
          aria-live="polite"
          className="text-sm font-medium text-green-600 dark:text-green-500"
        >
          Saved ✓
        </span>
      )}
      <span className="w-full text-xs text-zinc-500 sm:w-auto">
        Used only by filters with eligibility enabled.
      </span>
    </form>
  );
}
