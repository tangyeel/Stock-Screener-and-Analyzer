"""
screener/regime.py — Stage 0: Tiered Market Regime Filter (v2).

Replaces the binary index-on/off switch with breadth-based tiers so
genuine stock-level strength isn't blocked by index-level weakness.

Regime tiers (v2 spec §6.1):
  - strong_bull:       >= 60% above SMA200, VIX < 20    → 3 picks, RS≥70
  - neutral_selective: >= 40% above SMA200               → 2 picks, RS≥80
  - weak_selective:    >= 25% above SMA200               → 1 pick,  RS≥90
  - bearish:           < 25% above SMA200                → 1 pick,  RS≥95 (override only)

Bearish override (§6.2): stocks with RS≥95 and near 52w high can
still be picked in bearish regimes, flagged explicitly.

Sector cross-check (§6.3): a separate stage applied after pattern
detection (see sector_check.py).
"""

import json
import uuid
import logging
from datetime import date

from config import REGIME_TIERS
from db.database import get_connection

logger = logging.getLogger(__name__)


def market_regime_tier(
    pct_above_sma200: float,
    india_vix: float | None,
) -> dict:
    """
    Determine the regime tier from breadth and VIX.

    Args:
        pct_above_sma200: % of Nifty 100 stocks above 200DMA.
        india_vix:        India VIX value (can be None if fetch failed).

    Returns:
        {
            "tier":              str,
            "max_picks":         int,
            "rs_threshold":      int,
            "risk_multiplier":   float,
        }
    """
    if pct_above_sma200 >= REGIME_TIERS["strong_bull"]["pct_above_sma200_min"]:
        if india_vix is not None and india_vix < REGIME_TIERS["strong_bull"]["vix_max"]:
            return dict(REGIME_TIERS["strong_bull"])
        return dict(REGIME_TIERS["neutral_selective"])

    if pct_above_sma200 >= REGIME_TIERS["neutral_selective"]["pct_above_sma200_min"]:
        return dict(REGIME_TIERS["neutral_selective"])

    if pct_above_sma200 >= REGIME_TIERS["weak_selective"]["pct_above_sma200_min"]:
        return dict(REGIME_TIERS["weak_selective"])

    return dict(REGIME_TIERS["bearish"])


def allow_bearish_override(row: dict) -> bool:
    """
    In bearish regimes, allow stocks with exceptional relative strength.

    Conditions (v2 spec §6.2):
      - RS Rating >= 95
      - Close within 2% of 52-week high

    Returns True if the stock qualifies as a high-conviction override.
    """
    rs = row.get("rs_rating") or 0
    close = row.get("close")
    wk52_high = row.get("week52_high")

    if not close or not wk52_high or wk52_high <= 0:
        return False

    near_high = close >= wk52_high * 0.98
    return rs >= 95 and near_high


def check_market_regime(
    breadth: dict,
    india_vix: float | None,
    run_id: str,
    run_date: str = None,
) -> dict:
    """
    Evaluate market regime using breadth-based tiers.

    Args:
        breadth:   dict from calc_market_breadth() with keys:
                   pct_above_sma50, pct_above_sma200, nifty100_close,
                   nifty100_sma50, nifty100_sma200.
        india_vix: India VIX value or None.
        run_id:    Current run UUID for logging.
        run_date:  ISO date string. Defaults to today.

    Returns:
        {
            "tier":              str,
            "passed":            bool (False only in bearish with no override candidates),
            "max_picks":         int,
            "rs_threshold":      int,
            "risk_multiplier":   float,
            "pct_above_sma50":   float,
            "pct_above_sma200":  float,
            "india_vix":         float or None,
            "nifty100_close":    float or None,
            "nifty100_sma200":   float or None,
        }
    """
    if run_date is None:
        run_date = date.today().isoformat()

    pct_above_sma200 = breadth.get("pct_above_sma200", 0.0)
    pct_above_sma50  = breadth.get("pct_above_sma50", 0.0)

    tier_config = market_regime_tier(pct_above_sma200, india_vix)
    tier = tier_config["tier"]

    result = {
        "tier":                    tier,
        "passed":                  tier != "bearish",
        "max_picks":               tier_config["max_picks"],
        "rs_threshold":            tier_config["rs_threshold"],
        "risk_multiplier":         tier_config["risk_multiplier"],
        "pct_above_sma50":         pct_above_sma50,
        "pct_above_sma200":        pct_above_sma200,
        "india_vix":               india_vix,
        "nifty100_close":          breadth.get("nifty100_close"),
        "nifty100_sma50":          breadth.get("nifty100_sma50"),
        "nifty100_sma200":         breadth.get("nifty100_sma200"),
        "regime":                  tier,  # compat with old callers
    }

    _log_regime(run_id, run_date, result, override_candidates=None)

    logger.info(
        "Market regime: %s (%.1f%% above SMA200, VIX=%s, picks=%d, RS≥%d, risk=%.2f)",
        tier,
        pct_above_sma200,
        f"{india_vix:.1f}" if india_vix is not None else "N/A",
        result["max_picks"],
        result["rs_threshold"],
        result["risk_multiplier"],
    )

    return result


def _log_regime(
    run_id: str,
    run_date: str,
    result: dict,
    override_candidates: list[str] | None = None,
) -> None:
    """Write regime check result to regime_log table."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO regime_log
                (run_id, run_date,
                 index_close, index_sma50, index_sma200,
                 pct_above_sma50, pct_above_sma200, india_vix,
                 tier, max_picks, rs_threshold, risk_multiplier,
                 override_candidates, regime, passed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, run_date,
                result.get("nifty100_close"),
                result.get("nifty100_sma50"),
                result.get("nifty100_sma200"),
                result.get("pct_above_sma50"),
                result.get("pct_above_sma200"),
                result.get("india_vix"),
                result["tier"],
                result["max_picks"],
                result["rs_threshold"],
                result["risk_multiplier"],
                json.dumps(override_candidates) if override_candidates else None,
                result["tier"],
                1 if result["passed"] else 0,
            ),
        )
