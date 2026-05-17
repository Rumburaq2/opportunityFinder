type FilterFormProps = {
  action: (formData: FormData) => void | Promise<void>;
  initial?: {
    event_type: "any" | "discovereu" | "youth_exchange" | "training_course";
    country: string | null;
    date_from: string | null;
    date_to: string | null;
    active: boolean;
  };
  submitLabel: string;
  error?: string;
};

export function FilterForm({
  action,
  initial,
  submitLabel,
  error,
}: FilterFormProps) {
  const ev = initial?.event_type ?? "any";
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
          defaultValue={ev}
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
          htmlFor="country"
          className="block text-sm font-medium text-zinc-700 dark:text-zinc-300"
        >
          Country (optional)
        </label>
        <input
          id="country"
          name="country"
          type="text"
          maxLength={2}
          autoComplete="off"
          placeholder="DE"
          defaultValue={initial?.country ?? ""}
          className="mt-1 h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm uppercase dark:border-zinc-700 dark:bg-zinc-950"
        />
        <p className="mt-1 text-xs text-zinc-500">
          ISO 2-letter country code. Leave blank for any country.
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
