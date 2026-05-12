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


def _row_for_youth_exchange(item: dict) -> dict:
    # Adapter is responsible for the id prefix (e.g. "eyc:<rss_guid>").
    return {
        "id": item["id"],
        "source": "youth_exchange",
        "name": item.get("name") or "",
        "description": item.get("description") or "",
        "period_start": _to_date(item.get("period_start")),
        "period_end": _to_date(item.get("period_end")),
        "country": item.get("country") or None,
        "partner_countries": item.get("partner_countries") or None,
        "url": item.get("url") or None,
        "raw": item.get("raw"),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }


_ROW_BUILDERS = {
    "discovereu": _row_for_discovereu,
    "youth_exchange": _row_for_youth_exchange,
}


def seen_ids(candidate_ids: Iterable[str]) -> set[str]:
    """Return the subset of ids the system has previously processed.

    "Previously processed" = either stored in `events` (kept) OR recorded in
    `skipped_sources` (deliberately discarded — non-YE, already-ended, etc.).
    Adapters call this *before* invoking the LLM extractor so unchanged posts
    don't burn through the Gemini quota.
    """
    ids = list(candidate_ids)
    if not ids:
        return set()
    client = get_client()
    events_resp = client.table("events").select("id").in_("id", ids).execute()
    skipped_resp = (
        client.table("skipped_sources").select("source_id").in_("source_id", ids).execute()
    )
    return (
        {r["id"] for r in (events_resp.data or [])}
        | {r["source_id"] for r in (skipped_resp.data or [])}
    )


def mark_skipped(source_id: str, adapter: str, reason: str) -> None:
    """Record a non-retryable decision to discard a source row.

    Only call for decisions that won't change on a retry (e.g., the post is
    classified as non-YE, or its dates have passed). Do NOT call for transient
    failures like validator rejection or PDF fetch error — those should be
    retried next cycle.
    """
    client = get_client()
    client.table("skipped_sources").upsert(
        {"source_id": source_id, "adapter": adapter, "reason": reason},
        on_conflict="source_id",
        ignore_duplicates=True,
    ).execute()


def upsert_events(items: Iterable[dict], source: str) -> list[str]:
    """Upsert events into Supabase, returning ids of rows that were newly inserted.

    Existing rows still get `last_seen_at` refreshed; only their ids are
    excluded from the return value so callers can act on genuine novelty.
    """
    builder = _ROW_BUILDERS.get(source)
    if builder is None:
        raise ValueError(f"Unsupported source: {source!r}")

    rows = [builder(item) for item in items]
    if not rows:
        logging.info("upsert_events: no items to write (source=%s)", source)
        return []

    client = get_client()
    ids = [r["id"] for r in rows]

    existing = client.table("events").select("id").in_("id", ids).execute()
    existing_set = {r["id"] for r in (existing.data or [])}
    new_ids = [r_id for r_id in ids if r_id not in existing_set]

    client.table("events").upsert(rows, on_conflict="id").execute()

    logging.info(
        "upsert_events: wrote %d rows to Supabase (source=%s, %d new)",
        len(rows), source, len(new_ids),
    )
    return new_ids
