"""Phase 3d dispatcher: fan out new event matches to subscribed users via Telegram.

Algorithm (per docs/plan.md):
  1. RPC pending_notifications() — returns one row per (user, event, filter)
     match where the event was last seen in the past 7 days, the user has a
     linked Telegram chat_id, and we haven't already sent that event to them.
  2. For each row: send Telegram first, then upsert into notifications_sent.
     A failed send leaves the row absent → it reappears next cycle and retries.
     A failed insert after a successful send is the only theoretical duplicate
     case (acceptable for MVP).
  3. Sleep 0.04s between sends to stay under Telegram's 30 msg/s global cap.
"""
from __future__ import annotations

import logging
import time

from notifier import format_event_match, send_to_user
from supabase_client import get_client

# Global Telegram cap is 30 msg/s; 0.04s gives ~25 msg/s headroom.
_INTER_SEND_SLEEP_S = 0.04


def dispatch_pending() -> None:
    client = get_client()

    response = client.rpc("pending_notifications").execute()
    rows = response.data or []

    if not rows:
        logging.info("dispatch_pending: nothing to send")
        return

    logging.info("dispatch_pending: %d pending matches", len(rows))

    sent = 0
    failed = 0
    for row in rows:
        chat_id = row["telegram_chat_id"]
        user_id = row["user_id"]
        event_id = row["event_id"]
        filter_id = row.get("filter_id")

        message = format_event_match(row)

        try:
            send_to_user(chat_id, message)
        except Exception:
            logging.exception(
                "dispatch_pending: send failed user=%s event=%s",
                user_id, event_id,
            )
            failed += 1
            continue  # row stays absent from notifications_sent → retried next cycle

        try:
            client.table("notifications_sent").upsert(
                {
                    "user_id": user_id,
                    "event_id": event_id,
                    "filter_id": filter_id,
                },
                on_conflict="user_id,event_id",
                ignore_duplicates=True,
            ).execute()
            sent += 1
        except Exception:
            logging.exception(
                "dispatch_pending: insert failed after successful send "
                "(potential duplicate next cycle) user=%s event=%s",
                user_id, event_id,
            )

        time.sleep(_INTER_SEND_SLEEP_S)

    logging.info("dispatch_pending: sent=%d failed=%d", sent, failed)
