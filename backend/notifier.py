import logging
import os
import time

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
PAGE_URL = "https://youth.europa.eu/discovereu/meetups_en"

# Per-message retry budget on Telegram 429 responses. Telegram tells us how long
# to back off via parameters.retry_after; we honor it up to this many attempts.
_MAX_429_RETRIES = 3


def format_event_match(row: dict) -> str:
    """Format one matched event for a per-user dispatcher message.

    Input row comes from the pending_notifications() RPC and uses the
    event_* prefixed column names defined in migration 0005.
    """
    source = row.get("event_source") or ""
    header = (
        "DiscoverEU meetup match!"
        if source == "discovereu"
        else "Youth exchange match!"
        if source == "youth_exchange"
        else "Training course match!"
        if source == "training_course"
        else "New event match!"
    )
    name = row.get("event_name") or "(no title)"
    start = (row.get("event_period_start") or "?")[:10]
    end = (row.get("event_period_end") or "?")[:10]
    country = row.get("event_country") or "?"
    url = row.get("event_url") or PAGE_URL

    return (
        f"{header}\n\n"
        f"{name}\n"
        f"{start} to {end} | {country}\n"
        f"{url}"
    )


def send_to_user(chat_id: int, message: str) -> None:
    """Per-user Telegram send used by the dispatcher.

    Retries up to _MAX_429_RETRIES on HTTP 429, honoring the retry_after value
    Telegram returns. Other failures raise — the dispatcher catches and skips
    the notifications_sent insert so the row reappears next cycle.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    for attempt in range(_MAX_429_RETRIES):
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(
                    response.json().get("parameters", {}).get("retry_after", 1)
                )
            except (ValueError, TypeError):
                pass
            logging.warning(
                "Telegram 429 for chat=%s, sleeping %ds (attempt %d/%d)",
                chat_id, retry_after, attempt + 1, _MAX_429_RETRIES,
            )
            time.sleep(retry_after)
            continue
        response.raise_for_status()
        return

    raise RuntimeError(
        f"send_to_user: exhausted {_MAX_429_RETRIES} retries for chat={chat_id}"
    )
