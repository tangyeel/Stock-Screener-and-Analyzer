"""
Resolve free-text queries to an instrument in instrument_master.

Strategy:
  1. Exact ticker match
  2. Exact alias match (any alias in the comma-separated list)
  3. Fuzzy match across primary_name + aliases using rapidfuzz
  4. No match → return top suggestions
"""

import logging
from rapidfuzz import process, fuzz

from db.database import get_connection

logger = logging.getLogger(__name__)

CACHE: list[dict] | None = None


def _load_all():
    global CACHE
    if CACHE is not None:
        return CACHE
    with get_connection() as conn:
        CACHE = [dict(r) for r in conn.execute(
            "SELECT id, instrument_type, ticker, primary_name, aliases, sector, exchange "
            "FROM instrument_master WHERE is_active=1"
        ).fetchall()]
    return CACHE


def invalidate_cache():
    global CACHE
    CACHE = None


def resolve(query: str) -> dict:
    query_clean = query.strip().lower()
    instruments = _load_all()

    if not instruments:
        return {"match": None, "suggestions": [], "error": "No instruments in database — run population scripts first"}

    # 1. Exact ticker match
    for inst in instruments:
        if inst["ticker"].lower() == query_clean:
            return {"match": inst, "confidence": "exact", "method": "ticker"}

    # 2. Exact alias match
    for inst in instruments:
        aliases = [a.strip().lower() for a in (inst["aliases"] or "").split(",") if a.strip()]
        if query_clean in aliases:
            return {"match": inst, "confidence": "exact", "method": "alias"}

    # 3. Fuzzy match
    choices = {}
    for inst in instruments:
        text = f"{inst['primary_name']} {inst['aliases'] or ''}".lower()
        choices[inst["id"]] = text

    best = process.extractOne(query_clean, choices, scorer=fuzz.WRatio)
    if best and best[1] >= 70:
        matched = next(i for i in instruments if i["id"] == best[2])
        return {"match": matched, "confidence": "fuzzy", "score": best[1], "method": "fuzzy"}

    # 4. No confident match — return suggestions
    top3 = process.extract(query_clean, choices, scorer=fuzz.WRatio, limit=3)
    suggestions = []
    for t in top3:
        matched = next(i for i in instruments if i["id"] == t[2])
        if t[1] >= 40:
            suggestions.append({
                "ticker": matched["ticker"],
                "name": matched["primary_name"],
                "type": matched["instrument_type"],
                "score": t[1],
            })
    return {"match": None, "suggestions": suggestions}
