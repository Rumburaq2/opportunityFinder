"""Per-NGO source adapters. Each module is a self-contained source integration
(fetch + bespoke LLM prompt + dedup + per-source mapping).

Each adapter's `fetch()` returns a list of (source, item) pairs where `source`
is either "youth_exchange" or "training_course" — the LLM classifies each
post and the adapter routes it to the right bucket.

Adding a new NGO: write `adapters/<slug>.py` exposing `fetch()` and
`EXTRACTION_PROMPT`; append it to ADAPTERS below.
"""
from __future__ import annotations

from . import (
    adel,
    bfy,
    erasmusgram,
    europsky_dialog,
    eyc_breclav,
    mladiinfo,
    salto,
    yic,
    youthist,
    yysk,
)

ADAPTERS = [
    eyc_breclav,
    bfy,
    mladiinfo,
    salto,
    yysk,
    adel,
    europsky_dialog,
    yic,
    erasmusgram,
    youthist,
]
