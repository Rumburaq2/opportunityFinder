"""Per-NGO source adapters. Each module is a self-contained source integration
(fetch + bespoke LLM prompt + dedup + per-source mapping).

Adding a new NGO: write `adapters/<slug>.py` exposing `SOURCE`, `fetch()`,
and `EXTRACTION_PROMPT`; append it to ADAPTERS below.
"""
from __future__ import annotations

from . import bfy, eyc_breclav

ADAPTERS = [eyc_breclav, bfy]
