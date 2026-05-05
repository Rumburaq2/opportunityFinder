import Link from "next/link";

export default function Home() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-24">
      <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
        Find your next opportunity in Europe.
      </h1>
      <p className="mt-6 text-lg text-zinc-600 dark:text-zinc-400">
        Browse free DiscoverEU meet-ups and NGO-hosted Youth Exchanges, all in
        one place. Filtered Telegram alerts coming soon.
      </p>
      <div className="mt-10">
        <Link
          href="/events"
          className="inline-flex h-11 items-center rounded-full bg-zinc-900 px-6 text-sm font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          Browse events
        </Link>
      </div>
    </div>
  );
}
