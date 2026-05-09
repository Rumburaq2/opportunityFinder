import logging
import time

import azure.functions as func

from dispatcher import dispatch_pending
from events_writer import upsert_events
from notifier import format_notification, send_notification
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

    # --- Step 1: scrape + legacy owner notification ---
    # Wrapped so a scraper hiccup doesn't block the dispatcher (which can still
    # retry previously-failed sends from earlier cycles).
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
                "legacy notification. hash1=%s hash2=%s",
                hash_1[:12], hash_2[:12],
            )
        else:
            new_ids = upsert_events(meetups_1, "discovereu")
            if not new_ids:
                logging.info("No new meetups detected")
            else:
                new_ids_set = set(new_ids)
                added = [
                    m for m in meetups_1 if f"discovereu:{m['id']}" in new_ids_set
                ]
                logging.info("Change detected! %d new meetups", len(added))
                send_notification(format_notification(added))
                logging.info("Legacy owner notification sent")
    except Exception:
        logging.exception(
            "Scrape/legacy notification step failed (continuing to dispatcher)"
        )

    # --- Step 2: dispatcher (Phase 3d, additive) ---
    # Runs every cycle regardless of step 1's outcome so previously-failed sends
    # retry and late filter creations get picked up.
    try:
        dispatch_pending()
    except Exception:
        logging.exception("dispatch_pending step failed")
