"""Phase 4a (Wave 1): fetch info-pack PDFs linked from NGO posts.

NGO posts on WordPress typically link an info pack via Google Drive
(`drive.google.com/file/d/<id>/view`). This module:
  - rewrites Drive share URLs to direct-download form,
  - GETs the file with a short timeout and a real User-Agent,
  - confirms the response actually is a PDF (Drive sometimes returns an
    HTML "sign in" or "virus-scan warning" page for protected/huge files),
  - returns the raw bytes on success or None on any failure.

Fail-soft is intentional: the adapter falls back to text-only extraction
if no PDF is available, so a flaky Drive link never blocks ingestion.
"""
from __future__ import annotations

import logging
import re

import httpx

# 20 MB is the Gemini inline-PDF limit; NGO info packs are ~1–3 MB so this is
# only a safety cap.
_MAX_BYTES = 20 * 1024 * 1024
_TIMEOUT_S = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_DRIVE_SHARE_RE = re.compile(
    r"https?://drive\.google\.com/file/d/([A-Za-z0-9_-]+)(?:/[^?\s]*)?",
)


def _normalize_url(url: str) -> str:
    m = _DRIVE_SHARE_RE.match(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def fetch_pdf(url: str) -> bytes | None:
    direct = _normalize_url(url)
    try:
        with httpx.Client(
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(direct)
    except httpx.HTTPError as exc:
        logging.warning("pdf_fetcher: GET failed url=%s err=%s", url, exc)
        return None

    if response.status_code != 200:
        logging.warning(
            "pdf_fetcher: non-200 url=%s status=%d", url, response.status_code,
        )
        return None

    content_type = response.headers.get("content-type", "").lower()
    body = response.content

    # Drive's "scan warning" page is text/html and not what we want.
    if "application/pdf" not in content_type:
        # Some servers send octet-stream; sniff the magic bytes as a fallback.
        if not body.startswith(b"%PDF-"):
            logging.warning(
                "pdf_fetcher: response not a PDF url=%s content_type=%s prefix=%r",
                url, content_type, body[:8],
            )
            return None

    if len(body) > _MAX_BYTES:
        logging.warning(
            "pdf_fetcher: PDF too large url=%s bytes=%d (limit=%d)",
            url, len(body), _MAX_BYTES,
        )
        return None

    logging.info("pdf_fetcher: ok url=%s bytes=%d", url, len(body))
    return body
