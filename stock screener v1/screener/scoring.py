# FILE: screener/scoring.py
# STRATEGY NOTE: Smoothed RSI momentum scoring, rebalanced weights, and adjusted trade stop and target parameters for improved robustness.
"""
screener/scoring.py — Stage 4: Scoring & Ranking.

Scores each setup candidate on 7 weighted factors and returns the top picks.
Never pads to a minimum count — if 0 candidates qualify, 0 picks are returned.

Scoring weights (v3 spec):
    29% RS Rating (relative strength vs peers)
    21% Base tightness (lower consolidation CoV AND ATR/Price = better)
    14% Volume ratio (relative to 20-day avg, capped at 3×)
    13% Proximity to 52-week high (closer = more strength)
     8% RSI Momentum (controlled, peaking around 75)
    10% Sector RS Rating
     5% Delivery pct slope (rising institutional buying, capped)
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

    rs_rating           = safe("rs_rating", 50.0)
    volume              = safe("volume")
    avg_vol_20          = safe("avg_vol_20", 1.0)
    tightness_cov       = safe("consolidation_tightness", 0.1) # CoV
    atr_to_price_ratio  = safe("atr_to_price_ratio", 0.05) # New indicator for tightness
    close               = safe("close")
    week52_high         = safe("week52_high", close or 1.0)
    delivery_slope      = safe("delivery_pct_slope", 0.0)
    sector_rs           = safe("sector_rs_rating", 50.0)
    rsi                 = safe("rsi", 50.0)

    # Volume ratio (capped at 3.0 to prevent outliers dominating)
    vol_ratio = min(volume / avg_vol_20, 3.0) if avg_vol_20 > 0 else 0.0
    # Scale vol_ratio (0-3) to 0-100 for consistent weighting
    scaled_vol_ratio = vol_ratio * (100.0 / 3.0)

    # Proximity to 52-week high: close/high ratio (1.0 = at all-time high)
    proximity = (close / week52_high) if week52_high > 0 else 0.0
    # Scale proximity (0-1) to 0-100
    scaled_proximity = proximity * 100.0

    # Tightness score: lower CoV and ATR/Price ratio are better; invert and scale
    # CoV score: CoV near 0 → score near 100; CoV ≥ 0.25 → near 0
    tightness_cov_score = max(0.0, (0.25 - tightness_cov) / 0.25) * 100.0

    # ATR/Price Ratio score: ratio near 0 → score near 100; ratio ≥ 0.10 → near 0
    # A ratio of 0.10 means ATR is 10% of price, which is quite volatile for a tight base.
    tightness_atr_score = max(0.0, (0.10 - atr_to_price_ratio) / 0.10) * 100.0

    # Combine both tightness scores (simple average)
    combined_tightness_score = (tightness_cov_score + tightness_atr_score) / 2.0

    # Delivery percentage slope: capped at 0.10 (10%) positive slope to prevent overfitting
    # Scales the capped slope (e.g., 0.05 -> 5.0) for consistent weighting
    capped_delivery_slope_score = min(max(delivery_slope, 0.0), 0.10) * 100.0

    # RSI Momentum Score: controlled, smooth scaling, peaking around 75
    def get_rsi_score(rsi_val):
        if rsi_val < 50.0:
            return 0.0
        elif 50.0 <= rsi_val <= 75.0:
            return (rsi_val - 50.0) / 25.0  # Scales from 0 to 1 over 25 points
        elif 75.0 < rsi_val <= 90.0:
            return max(0.0, 1.0 - (rsi_val - 75.0) / 15.0) # Scales from 1 to 0 over 15 points
        else: # RSI > 90 or other edge cases
            return 0.0
    rsi_momentum_score = get_rsi_score(rsi) * 100.0 # Scale to 0-100

    # Composite score with rebalanced weights
    score = (
        rs_rating                   * 0.29 + # Increased weight
        combined_tightness_score    * 0.21 + # Increased weight
        scaled_vol_ratio            * 0.14 +
        scaled_proximity            * 0.13 +
        rsi_momentum_score          * 0.08 + # Decreased weight
        sector_rs                   * 0.10 +
        capped_delivery_slope_score * 0.05
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