import logging
import os

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
PAGE_URL = "https://youth.europa.eu/discovereu/meetups_en"


def format_notification(new_meetups: list[dict], old_meetups: list[dict]) -> str:
    old_ids = {m["id"] for m in old_meetups}
    added = [m for m in new_meetups if m["id"] not in old_ids]

    removed_ids = {m["id"] for m in new_meetups}
    removed = [m for m in old_meetups if m["id"] not in removed_ids]

    lines = ["DiscoverEU Meetups Update!"]
    lines.append(f"Total: {len(old_meetups)} -> {len(new_meetups)} meetups\n")

    if added:
        lines.append(f"New meetups ({len(added)}):")
        for m in added:
            start = m["period_start"][:10] if m["period_start"] else "?"
            end = m["period_end"][:10] if m["period_end"] else "?"
            country = m["country"] or "?"
            lines.append(f"  - {m['name']}")
            lines.append(f"    {start} to {end} | {country}")
            lines.append(f"    {m['url']}")

    if removed:
        lines.append(f"\nRemoved meetups ({len(removed)}):")
        for m in removed:
            lines.append(f"  - {m['name']}")

    lines.append(f"\nSee all meetups: {PAGE_URL}")
    return "\n".join(lines)


def send_notification(message: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    logging.info("Telegram notification sent successfully")
