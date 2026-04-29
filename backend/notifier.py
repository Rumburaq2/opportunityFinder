import logging
import os

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
PAGE_URL = "https://youth.europa.eu/discovereu/meetups_en"


def format_notification(added_meetups: list[dict]) -> str:
    lines = ["DiscoverEU Meetups Update!"]
    lines.append(f"New meetups ({len(added_meetups)}):")
    for m in added_meetups:
        start = m["period_start"][:10] if m["period_start"] else "?"
        end = m["period_end"][:10] if m["period_end"] else "?"
        country = m["country"] or "?"
        lines.append(f"  - {m['name']}")
        lines.append(f"    {start} to {end} | {country}")
        lines.append(f"    {m['url']}")

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
