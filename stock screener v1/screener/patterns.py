"""
screener/patterns.py — Stage 3: Setup Pattern Detection.

Detects two setup types:
    1. Pullback Setup:  Stock pulling back to SMA20 in an uptrend, RSI recovering
    2. Breakout Setup:  Stock near a prior high, tight base, volume confirmation

A stock can qualify for both; the higher-scoring one is used.
Every candidate is logged to filter_log with the pattern type and detail.
"""

import uuid
import json
import logging
from datetime import date

from db.database import get_connection

logger = logging.getLogger(__name__)

# Thresholds — tune these during shadow mode based on observed results
PULLBACK_SMA20_PROXIMITY = 0.03   # within 3% of SMA20
PULLBACK_RSI_LOW         = 40     # RSI floor for recovery signal
PULLBACK_RSI_HIGH        = 58     # RSI ceiling — not yet overbought
BREAKOUT_VOLUME_RATIO    = 1.5    # volume must be 1.5× avg to confirm
BREAKOUT_TIGHTNESS       = 0.05   # consolidation CoV < 5%
BREAKOUT_PIVOT_PROXIMITY = 0.03   # within 3% of recent high


def detect_pullback_setup(row: dict) -> dict:
    """
    Pullback setup: stock near SMA20, RSI in recovery zone (40–58).

    Best setups are in strong uptrends where price has pulled back
    to the rising 20-day MA without breaking the trend structure.

    Returns:
        {"detected": bool, "near_sma20": bool, "rsi_recovering": bool, "details": {...}}
    """
    def safe(k):
        try:
            return float(row.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    close  = safe("close")
    sma20  = safe("sma20")
    rsi14  = safe("rsi14")

    near_sma20 = (
        sma20 > 0 and
        abs(close - sma20) / close < PULLBACK_SMA20_PROXIMITY
    )
    rsi_recovering = PULLBACK_RSI_LOW <= rsi14 <= PULLBACK_RSI_HIGH

    detected = near_sma20 and rsi_recovering

    return {
        "detected":      detected,
        "near_sma20":    near_sma20,
        "rsi_recovering": rsi_recovering,
        "details": {
            "close":              close,
            "sma20":              sma20,
            "sma20_proximity_pct": round(abs(close - sma20) / close * 100, 2) if close else None,
            "rsi14":              rsi14,
            "rsi_range":          f"{PULLBACK_RSI_LOW}–{PULLBACK_RSI_HIGH}",
        },
    }


def detect_breakout_setup(row: dict, recent_high: float = None) -> dict:
    """
    Breakout setup: stock near a pivot high, tight base, volume confirmation.

    Uses week52_high as the pivot if recent_high is not provided.

    Returns:
        {"detected": bool, "volume_confirms": bool, "tight_base": bool,
         "near_pivot": bool, "pivot": float, "details": {...}}
    """
    def safe(k):
        try:
            return float(row.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    close       = safe("close")
    volume      = safe("volume")
    avg_vol_20  = safe("avg_vol_20")
    tightness   = safe("consolidation_tightness")
    week52_high = safe("week52_high")

    pivot = float(recent_high) if recent_high and recent_high > 0 else week52_high

    volume_ratio    = volume / avg_vol_20 if avg_vol_20 > 0 else 0
    volume_confirms = volume_ratio >= BREAKOUT_VOLUME_RATIO
    tight_base      = 0 < tightness < BREAKOUT_TIGHTNESS
    near_pivot      = pivot > 0 and close >= pivot * (1 - BREAKOUT_PIVOT_PROXIMITY)

    detected = volume_confirms and tight_base and near_pivot

    return {
        "detected":       detected,
        "volume_confirms": volume_confirms,
        "tight_base":      tight_base,
        "near_pivot":      near_pivot,
        "pivot":           round(pivot, 2),
        "details": {
            "close":          close,
            "volume":         volume,
            "avg_vol_20":     avg_vol_20,
            "volume_ratio":   round(volume_ratio, 2),
            "tightness":      round(tightness, 4),
            "week52_high":    week52_high,
            "pivot_used":     round(pivot, 2),
        },
    }


def run_pattern_filter(candidates: list[dict], run_id: str, log_results: bool = True) -> list[dict]:
    """
    Apply Stage 3 pattern detection to all trend-template passers.
    Logs every symbol's result if log_results is True. Returns symbols that matched at least one pattern.

    Each returned dict has two extra keys:
        "_setup_type": "pullback" | "breakout" | "both"
        "_pattern_detail": {...} — details of the winning pattern

    Args:
        candidates: list of dicts passing Stage 2
        run_id:     current run UUID
        log_results: If True, writes results to SQLite filter_log table.

    Returns:
        List of candidates that matched a setup pattern.
    """
    passed = []

    for row in candidates:
        symbol = row.get("symbol", "?")

        pullback = detect_pullback_setup(row)
        breakout = detect_breakout_setup(row)

        any_detected = pullback["detected"] or breakout["detected"]

        # Determine primary setup type (prefer breakout if both)
        if pullback["detected"] and breakout["detected"]:
            setup_type = "both"
        elif breakout["detected"]:
            setup_type = "breakout"
        elif pullback["detected"]:
            setup_type = "pullback"
        else:
            setup_type = "none"

        log_details = {
            "setup_type": setup_type,
            "pullback": pullback["details"],
            "pullback_detected": pullback["detected"],
            "breakout": breakout["details"],
            "breakout_detected": breakout["detected"],
        }

        if log_results:
            _log_pattern(run_id, symbol, any_detected, log_details)

        if any_detected:
            row = row.copy()
            # If both patterns match, use breakout (higher momentum)
            row["_setup_type"]     = "breakout" if breakout["detected"] else "pullback"
            row["_pattern_detail"] = breakout["details"] if breakout["detected"] else pullback["details"]
            row["_breakout_pivot"] = breakout.get("pivot") if breakout["detected"] else None
            passed.append(row)

    logger.info("Pattern filter: %d/%d matched a setup", len(passed), len(candidates))
    return passed


def _log_pattern(run_id: str, symbol: str, passed: bool, details: dict) -> None:
    from db.database import accumulate_filter_log
    accumulate_filter_log(run_id, symbol, "setup_pattern", passed, details)
