"""
screener/scoring.py — Stage 4: Scoring & Ranking.

Scores each setup candidate on 6 weighted factors and returns the top picks.
Never pads to a minimum count — if 0 candidates qualify, 0 picks are returned.

Scoring weights (v2 spec §7, Stage 4):
    28% RS Rating (relative strength vs peers)
    20% Volume ratio (relative to 20-day avg, capped at 3×)
    18% Base tightness (lower consolidation CoV = better)
    14% Proximity to 52-week high (closer = more strength)
    12% Sector RS Rating
     8% Delivery pct slope (rising institutional buying)
"""

import logging
from config import MAX_PICKS

logger = logging.getLogger(__name__)


def score_candidate(row: dict) -> float:
    """
    Compute a composite score for a single candidate.
    Higher score = stronger setup.

    Args:
        row: dict with combined price + indicator fields, plus _setup_type from patterns.py

    Returns:
        Float score (typically 0–100 range).
    """
    def safe(k, default=0.0):
        try:
            v = row.get(k)
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    rs_rating   = safe("rs_rating", 50)
    volume      = safe("volume")
    avg_vol_20  = safe("avg_vol_20", 1)
    tightness   = safe("consolidation_tightness", 0.1)
    close       = safe("close")
    week52_high = safe("week52_high", close or 1)
    delivery_slope = safe("delivery_pct_slope", 0)
    sector_rs   = safe("sector_rs_rating", 50)   # defaults to neutral if not populated

    # Volume ratio (capped at 3× to prevent outliers dominating)
    vol_ratio = min(volume / avg_vol_20, 3) if avg_vol_20 > 0 else 0

    # Proximity to 52-week high: close/high ratio (1.0 = at all-time high)
    proximity = (close / week52_high) if week52_high > 0 else 0

    # Tightness score: lower CoV is better; invert and scale
    # CoV near 0 → tightness_score near 100; CoV ≥ 0.2 → near 0
    tightness_score = max(0, (0.2 - tightness) / 0.2) * 100

    score = (
        rs_rating   * 0.28 +
        vol_ratio   * 20   * 0.20 +   # scale: 3× vol → 60 pts; ×0.20 → contributes up to 12
        tightness_score     * 0.18 +
        proximity   * 100  * 0.14 +
        sector_rs          * 0.12 +
        max(delivery_slope, 0) * 10 * 0.08
    )

    return round(score, 2)


def rank_and_select(candidates: list[dict], max_picks: int = MAX_PICKS) -> list[dict]:
    """
    Score all candidates, sort descending, return top N.
    Never returns more than max_picks, but may return fewer if less qualify.

    Args:
        candidates: list of dicts passing all filter stages
        max_picks:  maximum setups to return (default from config)

    Returns:
        Sorted list of top candidates with '_score' key added.
    """
    if not candidates:
        logger.info("No candidates to score — returning empty list.")
        return []

    for row in candidates:
        row["_score"] = score_candidate(row)

    sorted_candidates = sorted(candidates, key=lambda r: r["_score"], reverse=True)
    picks = sorted_candidates[:max_picks]

    logger.info(
        "Scoring complete: %d candidates → top %d selected. Scores: %s",
        len(candidates),
        len(picks),
        [f"{p.get('symbol')} ({p.get('_score')})" for p in picks],
    )

    return picks
