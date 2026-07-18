# FILE: screener/trade_params.py
"""
screener/trade_params.py — Entry / Stop / Target / Position Size calculator.

Uses ATR-based risk to set stops and a minimum 2.25:1 R:R for targets.
Hard caps stop at 8% from entry to prevent oversized risk on low-ATR stocks.
Adjusts target downward if 52-week high acts as obvious resistance.

Position sizing is regime-adjusted (v2 spec §8): risk_multiplier scales
the base risk % down in weaker market regimes.

Writes final picks to daily_screens table.
"""

import uuid
import logging
from datetime import date

# Constants for ATR stop and R:R optimization
BREAKOUT_ATR_MULTIPLIER = 1.7
PULLBACK_ATR_MULTIPLIER = 2.2
OPTIMIZED_REWARD_RISK_RATIO = 2.25

from config import CAPITAL, RISK_PER_TRADE_PCT, HARD_STOP_PCT # REWARD_RISK_RATIO is now overridden

logger = logging.getLogger(__name__)


def calculate_trade_params(row: dict, setup_type: str, recent_high: float = None,
                            disable_52w_cap: bool = False) -> dict:
    """
    Compute entry, stop, target, and R:R for a single setup.

    Breakout setup:
        Entry = pivot_high × 1.002  (buy just above the breakout level)
        Stop  = entry − (BREAKOUT_ATR_MULTIPLIER × ATR14)

    Pullback setup:
        Entry = close × 1.005       (buy a fraction above last close)
        Stop  = entry − (PULLBACK_ATR_MULTIPLIER × ATR14)  (wider stop for deeper pullbacks)

    Hard cap: stop never more than 8% below entry.
    Target:   entry + (OPTIMIZED_REWARD_RISK_RATIO × risk). Optionally capped at 52-week high.

    Args:
        row:            dict with close, atr14, week52_high
        setup_type:     "breakout" | "pullback"
        recent_high:    optional pivot high for breakout entry (defaults to week52_high)
        disable_52w_cap: if True, do NOT clip target at 52-week high

    Returns:
        dict with entry, stop, target, risk_pct, reward_risk_ratio
        or None if ATR is missing/zero.
    """
    def safe(k):
        try:
            v = row.get(k)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    atr   = safe("atr14")
    close = safe("close")
    week52_high = safe("week52_high")

    if not atr or atr <= 0 or not close or close <= 0:
        logger.warning(
            "Cannot compute trade params for %s — atr=%.4f close=%.2f",
            row.get("symbol", "?"), atr or 0, close or 0,
        )
        return None

    if setup_type == "breakout":
        pivot = float(recent_high) if recent_high and recent_high > 0 else (week52_high or close)
        entry = round(pivot * 1.002, 2)
        stop  = round(entry - (BREAKOUT_ATR_MULTIPLIER * atr), 2)
    else:  # pullback
        entry = round(close * 1.005, 2)
        stop  = round(entry - (PULLBACK_ATR_MULTIPLIER * atr), 2)

    # Hard cap: never risk more than HARD_STOP_PCT from entry
    hard_floor = round(entry * (1 - HARD_STOP_PCT), 2)
    stop = max(stop, hard_floor)

    risk = round(entry - stop, 2)
    if risk <= 0:
        logger.warning("Risk is zero or negative for %s — skipping.", row.get("symbol", "?"))
        return None

    target = round(entry + (OPTIMIZED_REWARD_RISK_RATIO * risk), 2)

    # Optionally clip target at 52-week high if it's sitting in the way
    if not disable_52w_cap and week52_high and entry < week52_high < target:
        target = round(week52_high, 2)

    actual_rr = round((target - entry) / risk, 2)
    risk_pct  = round((risk / entry) * 100, 2)

    return {
        "entry":             entry,
        "stop":              stop,
        "target":            target,
        "risk_pct":          risk_pct,
        "reward_risk_ratio": actual_rr,
    }


def position_size(entry: float, stop: float,
                  capital: float = CAPITAL,
                  risk_pct: float = RISK_PER_TRADE_PCT) -> int:
    """
    Calculate number of shares to buy for a given risk level.

    risk_pct % of capital is risked on this trade.
    Share count = risk_amount / (entry - stop).

    Returns 0 if entry ≤ stop or inputs are invalid.
    """
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return 0
    risk_amount = capital * (risk_pct / 100)
    return max(0, int(risk_amount / risk_per_share))


def build_trade_output(
    row: dict,
    params: dict,
    run_id: str,
    run_date: str = None,
    risk_multiplier: float = 1.0,
    is_override: bool = False,
    regime_tier: str = None,
) -> dict:
    """
    Combine candidate row + trade params into the final output dict.
    Writes to daily_screens and returns the complete pick dict.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    symbol     = row.get("symbol", "?")
    setup_type = row.get("_setup_type", "pullback")
    rs_rating  = row.get("rs_rating") or 0
    sector_rs  = row.get("sector_rs_rating") or 0
    score      = row.get("_score") or 0
    regime     = row.get("_market_regime", "bullish")
    tier       = regime_tier or regime

    base_risk_pct = RISK_PER_TRADE_PCT
    effective_risk_pct = round(base_risk_pct * risk_multiplier, 2)
    shares = position_size(params["entry"], params["stop"], risk_pct=effective_risk_pct)

    screen_id = str(uuid.uuid4())

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_screens (
                id, run_id, screen_date, symbol, setup_type,
                entry, stop, target, risk_pct, reward_risk_ratio,
                rs_rating, sector_rs_rating, score, shares_suggested,
                market_regime, regime_tier, is_override, effective_risk_pct,
                sent_to_telegram
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                screen_id, run_id, run_date, symbol, setup_type,
                params["entry"], params["stop"], params["target"],
                params["risk_pct"], params["reward_risk_ratio"],
                rs_rating, sector_rs, score, shares,
                regime, tier, 1 if is_override else 0, effective_risk_pct,
            ),
        )

    return {
        "screen_id":          screen_id,
        "symbol":             symbol,
        "setup_type":         setup_type,
        "entry":              params["entry"],
        "stop":               params["stop"],
        "target":             params["target"],
        "risk_pct":           params["risk_pct"],
        "reward_risk_ratio":  params["reward_risk_ratio"],
        "rs_rating":          rs_rating,
        "sector_rs_rating":   sector_rs,
        "score":              score,
        "shares":             shares,
        "effective_risk_pct": effective_risk_pct,
        "market_regime":      regime,
        "regime_tier":        tier,
        "is_override":        is_override,
    }


def process_picks(top_candidates: list[dict], run_id: str,
                  run_date: str = None, market_regime: str = "bullish",
                  risk_multiplier: float = 1.0, regime_tier: str = "bullish",
                  is_override: bool = False) -> list[dict]:
    """
    Compute trade parameters for each top candidate and write to daily_screens.

    Args:
        top_candidates:  list of scored candidate dicts from scoring.py
        run_id:          current run UUID
        run_date:        ISO date string
        market_regime:   regime string for labelling in daily_screens
        risk_multiplier: regime-adjusted scaling factor (v2 spec §8)
        regime_tier:     specific tier name from regime check
        is_override:     whether this is a bearish override pick

    Returns:
        List of final pick dicts ready for Telegram formatting.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    picks = []
    for row in top_candidates:
        row["_market_regime"] = market_regime
        setup_type  = row.get("_setup_type", "pullback")
        recent_high = row.get("_breakout_pivot")

        params = calculate_trade_params(row, setup_type, recent_high)
        if params is None:
            logger.warning("Skipping %s — could not compute trade params.", row.get("symbol"))
            continue

        pick = build_trade_output(
            row, params, run_id, run_date,
            risk_multiplier=risk_multiplier,
            is_override=is_override,
            regime_tier=regime_tier,
        )
        picks.append(pick)
        logger.info(
            "Pick: %s | %s | Entry ₹%.2f | Stop ₹%.2f | Target ₹%.2f | RR %.1f | RS %d | risk=%.0f%%",
            pick["symbol"], pick["setup_type"],
            pick["entry"], pick["stop"], pick["target"],
            pick["reward_risk_ratio"], pick["rs_rating"],
            pick["effective_risk_pct"] * 100,
        )

    return picks