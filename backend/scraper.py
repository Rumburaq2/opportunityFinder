import hashlib
import json
import logging
from datetime import datetime

import requests

BASE_URL = "https://youth.europa.eu/api/rest/eyp/v1/search_en"
PAGE_URL = "https://youth.europa.eu/discovereu/meetups_en"

HEADERS = {
    "Referer": PAGE_URL,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def fetch_meetups(year: int) -> list[dict]:
    params = {
        "visible": "true",
        "filters[period][start][value]": f"{year}-01-01T00:00:00",
        "filters[period][start][operator]": ">=",
        "filters[period][end][value]": f"{year}-12-31T23:59:59",
        "filters[period][end][operator]": "<=",
        "filters[visible]": "true",
        "type": "Meetup",
        "no_score": "true",
        "sort[period.start]": "asc",
        "sort[period.end]": "asc",
        "from": "0",
        "size": "100",
    }

    response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()

    data = response.json()
    hits = data.get("hits", {}).get("hits", [])

    meetups = []
    for hit in hits:
        source = hit.get("_source", {})
        period = source.get("period", {})
        meetups.append({
            "id": hit.get("_id", ""),
            "name": source.get("name", ""),
            "period_start": period.get("start", ""),
            "period_end": period.get("end", ""),
            "country": source.get("address", {}).get("country", ""),
            "url": "https://youth.europa.eu" + source.get("url", ""),
        })

    return sorted(meetups, key=lambda m: m["id"])


def get_all_meetups() -> list[dict]:
    now = datetime.utcnow()
    meetups = fetch_meetups(now.year)

    # Also fetch next year in November/December to catch upcoming meetups early
    if now.month >= 11:
        next_year_meetups = fetch_meetups(now.year + 1)
        existing_ids = {m["id"] for m in meetups}
        for m in next_year_meetups:
            if m["id"] not in existing_ids:
                meetups.append(m)
        meetups = sorted(meetups, key=lambda m: m["id"])

    logging.info("Fetched %d meetups for year(s) starting %d", len(meetups), now.year)
    return meetups


def compute_hash(meetups: list[dict]) -> str:
    canonical = json.dumps(meetups, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
