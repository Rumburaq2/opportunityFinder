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
    """Upsert events into Supabase, returning ids of rows that were newly inserted.

    Existing rows still get `last_seen_at` refreshed; only their ids are
    excluded from the return value so callers can act on genuine novelty.
    """
    if source != "discovereu":
        # youth_exchange (RSS) shape lands in Phase 4
        raise ValueError(f"Unsupported source: {source!r}")

    rows = [_row_for_discovereu(item) for item in items]
    if not rows:
        logging.info("upsert_events: no items to write (source=%s)", source)
        return []

    client = get_client()
    ids = [r["id"] for r in rows]

    existing = client.table("events").select("id").in_("id", ids).execute()
    existing_ids = {r["id"] for r in (existing.data or [])}
    new_ids = [r_id for r_id in ids if r_id not in existing_ids]

    client.table("events").upsert(rows, on_conflict="id").execute()

    logging.info(
        "upsert_events: wrote %d rows to Supabase (source=%s, %d new)",
        len(rows), source, len(new_ids),
    )
    return new_ids
