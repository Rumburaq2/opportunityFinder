import logging
import time

import azure.functions as func

from adapters import ADAPTERS
from dispatcher import dispatch_pending
from events_writer import upsert_events
from scraper import compute_hash, get_all_meetups

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 * * * *",  # Every hour, on the hour
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def check_meetups(timer: func.TimerRequest) -> None:
    logging.info("DiscoverEU meetup check started")

    # --- Step 1: DiscoverEU scrape + upsert ---
    # Wrapped so a scraper hiccup doesn't block adapters or the dispatcher
    # (which can still retry previously-failed sends from earlier cycles).
    # The 30s double-fetch guards against transient API noise creating
    # spurious event rows that the dispatcher would then notify on.
    try:
        meetups_1 = get_all_meetups()
        hash_1 = compute_hash(meetups_1)
        logging.info(
            "First fetch: %d meetups, hash=%s", len(meetups_1), hash_1[:12]
        )

        logging.info("Waiting 30 seconds before second fetch...")
        time.sleep(30)

        meetups_2 = get_all_meetups()
        hash_2 = compute_hash(meetups_2)
        logging.info(
            "Second fetch: %d meetups, hash=%s", len(meetups_2), hash_2[:12]
        )

        if hash_1 != hash_2:
            logging.warning(
                "Transient difference detected between two fetches — skipping "
                "upsert. hash1=%s hash2=%s",
                hash_1[:12], hash_2[:12],
            )
        else:
            new_ids = upsert_events(meetups_1, "discovereu")
            logging.info(
                "DiscoverEU upsert: %d total, %d new", len(meetups_1), len(new_ids)
            )
    except Exception:
        logging.exception(
            "DiscoverEU scrape step failed (continuing to adapters)"
        )

    # --- Step 2: NGO adapters (Phase 4a) ---
    # Each adapter is isolated: a single broken source must not block the
    # others or the dispatcher. Adapters dedup against `events` internally so
    # re-running this loop is cheap.
    for adapter in ADAPTERS:
        try:
            items = adapter.fetch()
            upsert_events(items, adapter.SOURCE)
        except Exception:
            logging.exception(
                "adapter %s failed (continuing)", getattr(adapter, "__name__", "?")
            )

    # --- Step 3: dispatcher (Phase 3d, additive) ---
    # Runs every cycle regardless of earlier outcomes so previously-failed
    # sends retry and late filter creations get picked up.
    try:
        dispatch_pending()
    except Exception:
        logging.exception("dispatch_pending step failed")
