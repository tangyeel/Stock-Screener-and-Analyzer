"""
screener/filters.py — Stage 1 (Liquidity) and Stage 2 (Trend Template).

Stage 1: Liquidity Filter
    - Daily turnover > ₹5 Cr (close × avg_vol_20)
    - Price > ₹30

Stage 2: Trend Template (Minervini)
    9 conditions, 7 must pass:
    1. Close > SMA150
    2. Close > SMA200
    3. SMA150 > SMA200
    4. SMA50 > SMA150
    5. Close > SMA50
    6. Close ≥ 125% of 52-week low
    7. Close ≥ 75% of 52-week high
    8. SMA200 slope > 0 (trending up)
    9. RS Rating ≥ 70

Every symbol's pass/fail at both stages is logged to filter_log with
the full condition breakdown as JSON — so you can always answer
"why didn't stock X show up today" without re-running anything.
"""

import uuid
import json
import logging
from datetime import date

import pandas as pd

from config import (
    MIN_TURNOVER, MIN_PRICE, RS_THRESHOLD, TREND_CONDITIONS_REQUIRED
)
from db.database import get_connection

logger = logging.getLogger(__name__)


# ── Stage 1: Liquidity ────────────────────────────────────────────────────────

def passes_liquidity(row: dict) -> bool:
    """
    Returns True if the symbol meets minimum liquidity requirements.

    Args:
        row: dict with keys: close, avg_vol_20
    """
    try:
        close     = float(row.get("close") or 0)
        avg_vol   = float(row.get("avg_vol_20") or 0)
        turnover  = close * avg_vol

        return turnover > MIN_TURNOVER and close > MIN_PRICE
    except (TypeError, ValueError):
        return False


# ── Stage 2: Trend Template ───────────────────────────────────────────────────

def passes_trend_template(row: dict, rs_threshold: int = None, trend_conditions_required: int = None) -> dict:
    """
    Evaluate all 9 Minervini Trend Template conditions.

    Args:
        row:          dict with indicator keys from daily_indicators + close/volume from daily_prices.
        rs_threshold: RS Rating minimum from regime tier. Defaults to config.RS_THRESHOLD.
        trend_conditions_required: Minimum conditions that must pass. Defaults to config.TREND_CONDITIONS_REQUIRED.

    Returns:
        {
            "passed": bool,
            "conditions_met": int,
            "conditions": {condition_name: bool, ...}
        }
    """
    if rs_threshold is None:
        rs_threshold = RS_THRESHOLD

    if trend_conditions_required is None:
        trend_conditions_required = TREND_CONDITIONS_REQUIRED

    def safe(key: str) -> float:
        val = row.get(key)
        try:
            return float(val) if val is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    close       = safe("close")
    sma50       = safe("sma50")
    sma150      = safe("sma150")
    sma200      = safe("sma200")
    sma200_slope = safe("sma200_slope")
    week52_high = safe("week52_high")
    week52_low  = safe("week52_low")
    rs_rating   = int(row.get("rs_rating") or 0)

    conditions = {
        "above_sma150":         close  > sma150  and sma150  > 0,
        "above_sma200":         close  > sma200  and sma200  > 0,
        "sma150_above_sma200":  sma150 > sma200  and sma200  > 0,
        "sma50_above_sma150":   sma50  > sma150  and sma150  > 0,
        "above_sma50":          close  > sma50   and sma50   > 0,
        "above_25pct_off_low":  week52_low  > 0 and close >= 1.25 * week52_low,
        "within_25pct_of_high": week52_high > 0 and close >= 0.75 * week52_high,
        "sma200_trending_up":   sma200_slope > 0,
        "rs_meets_regime_threshold": rs_rating >= rs_threshold,
    }

    conditions_met = sum(conditions.values())
    passed = conditions_met >= trend_conditions_required

    return {
        "passed":         passed,
        "conditions_met": conditions_met,
        "conditions":     conditions,
    }


# ── Filter Log Writer ─────────────────────────────────────────────────────────

def log_filter(run_id: str, symbol: str, stage: str, passed: bool, details: dict) -> None:
    """Write a single symbol's filter result to filter_log."""
    from db.database import accumulate_filter_log
    accumulate_filter_log(run_id, symbol, stage, passed, details)


# ── Batch Runners ─────────────────────────────────────────────────────────────

def run_liquidity_filter(candidates: list[dict], run_id: str) -> list[dict]:
    """
    Apply Stage 1 liquidity filter to a list of candidate rows.
    Logs every symbol's result. Returns symbols that passed.

    Args:
        candidates: list of dicts, each containing at least {symbol, close, avg_vol_20}
        run_id:     current run UUID

    Returns:
        Filtered list of dicts that passed liquidity.
    """
    passed = []
    for row in candidates:
        symbol  = row.get("symbol", "?")
        ok      = passes_liquidity(row)
        turnover = float(row.get("close") or 0) * float(row.get("avg_vol_20") or 0)
        log_filter(
            run_id, symbol, "liquidity", ok,
            {
                "close":     row.get("close"),
                "avg_vol_20": row.get("avg_vol_20"),
                "turnover_cr": round(turnover / 1e7, 2),
                "turnover_threshold_cr": MIN_TURNOVER / 1e7,
            },
        )
        if ok:
            passed.append(row)

    logger.info("Liquidity filter: %d/%d passed", len(passed), len(candidates))
    return passed


def run_trend_template_filter(candidates: list[dict], run_id: str,
                                rs_threshold: int = None,
                                trend_conditions_required: int = None,
                                log_results: bool = True) -> list[dict]:
    """
    Apply Stage 2 Trend Template filter.
    Logs every symbol's result with full condition breakdown if log_results is True.
    Returns symbols that passed.

    Args:
        candidates:  list of dicts with all indicator fields
        run_id:      current run UUID
        rs_threshold: RS Rating minimum from regime tier. Defaults to config.RS_THRESHOLD.
        trend_conditions_required: Minimum conditions that must pass. Defaults to config.TREND_CONDITIONS_REQUIRED.
        log_results: If True, writes results to SQLite filter_log table.

    Returns:
        Filtered list of dicts that passed the trend template.
    """
    if rs_threshold is None:
        rs_threshold = RS_THRESHOLD

    if trend_conditions_required is None:
        trend_conditions_required = TREND_CONDITIONS_REQUIRED

    passed = []
    for row in candidates:
        symbol = row.get("symbol", "?")
        result = passes_trend_template(row, rs_threshold=rs_threshold, trend_conditions_required=trend_conditions_required)
        if log_results:
            log_filter(
                run_id, symbol, "trend_template", result["passed"],
                {
                    "conditions_met": result["conditions_met"],
                    "required":       trend_conditions_required,
                    "conditions":     result["conditions"],
                    "rs_rating":      row.get("rs_rating"),
                    "rs_threshold":   rs_threshold,
                },
            )
        if result["passed"]:
            row["_trend_conditions_met"] = result["conditions_met"]
            passed.append(row)

    logger.info("Trend template filter: %d/%d passed", len(passed), len(candidates))
    return passed


def load_indicator_rows(run_date: str = None) -> list[dict]:
    """
    Load the latest indicator rows for all active symbols, joined with
    their latest price row. Used as the starting pool for the screening funnel.

    Args:
        run_date: ISO date string. Defaults to today.

    Returns:
        List of dicts with combined price + indicator fields.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                di.symbol, di.date,
                di.sma20, di.sma50, di.sma150, di.sma200, di.sma200_slope,
                di.atr14, di.rsi14, di.avg_vol_20,
                di.week52_high, di.week52_low,
                di.consolidation_tightness, di.delivery_pct_slope,
                di.rs_rating, di.sector_rs_rating,
                dp.open, dp.high, dp.low, dp.close, dp.volume, dp.delivery_pct
            FROM daily_indicators di
            JOIN daily_prices dp
                ON di.symbol = dp.symbol AND di.date = dp.date
            JOIN nifty100_constituents nc
                ON di.symbol = nc.symbol AND nc.is_active = 1
            WHERE di.date = ?
            ORDER BY di.symbol
            """,
            (run_date,),
        ).fetchall()

    result = [dict(r) for r in rows]
    logger.info("Loaded %d indicator rows for %s", len(result), run_date)
    return result
