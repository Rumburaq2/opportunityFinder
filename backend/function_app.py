import logging
import time

import azure.functions as func

from events_writer import upsert_events
from notifier import format_notification, send_notification
from scraper import compute_hash, get_all_meetups
from state import load_state, save_state, update_last_checked

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 * * * *",  # Every hour, on the hour
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def check_meetups(timer: func.TimerRequest) -> None:
    logging.info("DiscoverEU meetup check started")

    # --- First fetch ---
    meetups_1 = get_all_meetups()
    hash_1 = compute_hash(meetups_1)
    logging.info("First fetch: %d meetups, hash=%s", len(meetups_1), hash_1[:12])

    # --- Wait 30s and fetch again to rule out transient changes ---
    logging.info("Waiting 30 seconds before second fetch...")
    time.sleep(30)

    meetups_2 = get_all_meetups()
    hash_2 = compute_hash(meetups_2)
    logging.info("Second fetch: %d meetups, hash=%s", len(meetups_2), hash_2[:12])

    if hash_1 != hash_2:
        logging.warning(
            "Transient difference detected between two fetches — skipping notification. "
            "hash1=%s hash2=%s", hash_1[:12], hash_2[:12]
        )
        update_last_checked()
        return

    # --- Shadow-write to Supabase (Phase 1a, additive) ---
    # Failure here must not affect the legacy owner-notification flow below.
    try:
        upsert_events(meetups_1, "discovereu")
    except Exception:
        logging.exception("Shadow write to Supabase failed (legacy flow continues)")

    # --- Both fetches agree — compare against stored state ---
    stored_state = load_state()

    if stored_state is None:
        logging.info("First run — saving initial state (%d meetups)", len(meetups_1))
        save_state(hash_1, meetups_1)
        return

    stored_hash = stored_state.get("hash", "")

    if hash_1 == stored_hash:
        logging.info("No changes detected")
        update_last_checked()
        return

    # --- Real change confirmed ---
    old_meetups = stored_state.get("meetups", [])
    logging.info(
        "Change detected! Old hash=%s, new hash=%s. Old count=%d, new count=%d",
        stored_hash[:12], hash_1[:12], len(old_meetups), len(meetups_1),
    )

    message = format_notification(meetups_1, old_meetups)
    send_notification(message)

    save_state(hash_1, meetups_1)
    logging.info("State updated and notification sent")
