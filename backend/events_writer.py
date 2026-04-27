from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from supabase_client import get_client


def _to_date(value: str | None) -> str | None:
    if not value or len(value) < 10:
        return None
    return value[:10]


def _row_for_discovereu(item: dict) -> dict:
    return {
        "id": f"discovereu:{item['id']}",
        "source": "discovereu",
        "name": item.get("name") or "",
        "description": "",
        "period_start": _to_date(item.get("period_start")),
        "period_end": _to_date(item.get("period_end")),
        "country": item.get("country") or None,
        "url": item.get("url") or None,
        "raw": item,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_events(items: Iterable[dict], source: str) -> list[str]:
    if source != "discovereu":
        # youth_exchange (RSS) shape lands in Phase 4
        raise ValueError(f"Unsupported source: {source!r}")

    rows = [_row_for_discovereu(item) for item in items]
    if not rows:
        logging.info("upsert_events: no items to write (source=%s)", source)
        return []

    client = get_client()
    response = client.table("events").upsert(rows, on_conflict="id").execute()

    ids = [r["id"] for r in (response.data or [])]
    logging.info("upsert_events: wrote %d rows to Supabase (source=%s)", len(ids), source)
    return ids
