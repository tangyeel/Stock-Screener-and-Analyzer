"""
Support/resistance levels, VWAP, and pivot points.

Computes:
  - Daily VWAP (rolling 20-day anchored)
  - Classic pivot points (P, R1, R2, S1, S2)
  - Key levels from recent swing highs/lows (last 50 candles)
  - Bollinger Band squeeze detection
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_vwap(df: pd.DataFrame, window: int = 20) -> float | None:
    if len(df) < window:
        return None
    recent = df.iloc[-window:]
    tp = (recent["high"] + recent["low"] + recent["close"]) / 3
    vwap = (tp * recent["volume"]).sum() / recent["volume"].sum()
    return round(float(vwap), 2)


def calc_pivots(df: pd.DataFrame) -> dict:
    if len(df) < 1:
        return {}
    last = df.iloc[-1]
    high = float(last["high"])
    low = float(last["low"])
    close = float(last["close"])
    p = (high + low + close) / 3
    return {
        "pivot": round(p, 2),
        "r1": round(2 * p - low, 2),
        "r2": round(p + (high - low), 2),
        "s1": round(2 * p - high, 2),
        "s2": round(p - (high - low), 2),
    }


def find_swing_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    """Find nearest support and resistance from recent swing highs/lows."""
    if len(df) < lookback:
        lookback = len(df)
    recent = df.iloc[-lookback:]
    close = float(recent["close"].iloc[-1])
    highs = recent["high"].values
    lows = recent["low"].values

    # Find swing highs (peaks) with simple comparison
    swing_highs = []
    swing_lows = []
    for i in range(2, len(recent) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-2:i+3]):
            swing_lows.append(lows[i])

    if not swing_highs and not swing_lows:
        return {}

    # Nearest resistance above close
    above = [h for h in swing_highs if h > close]
    nearest_resistance = min(above) if above else None

    # Nearest support below close
    below = [l for l in swing_lows if l < close]
    nearest_support = max(below) if below else None

    return {
        "nearest_resistance": round(float(nearest_resistance), 2) if nearest_resistance else None,
        "nearest_support": round(float(nearest_support), 2) if nearest_support else None,
        "recent_high": round(float(max(highs)), 2),
        "recent_low": round(float(min(lows)), 2),
    }


def detect_bb_squeeze(df: pd.DataFrame) -> dict | None:
    """Check if Bollinger Bands are narrowing (potential breakout setup)."""
    if len(df) < 50:
        return None
    bb_width = df["bb_upper"] - df["bb_lower"]
    current_width = float(bb_width.iloc[-1])
    avg_width = float(bb_width.iloc[-50:].mean())
    ratio = current_width / avg_width if avg_width else 1
    if ratio < 0.8:
        return {
            "squeeze": True,
            "ratio": round(ratio, 2),
            "advice": "🔊 Bollinger squeeze — potential breakout imminent",
        }
    return None


def calc_all(df: pd.DataFrame) -> dict:
    result = {}
    vwap = calc_vwap(df)
    if vwap:
        result["vwap"] = vwap
        close = float(df["close"].iloc[-1])
        result["price_vs_vwap"] = "above" if close > vwap else "below"

    pivots = calc_pivots(df)
    if pivots:
        result.update(pivots)

    swings = find_swing_levels(df)
    if swings:
        result.update(swings)

    squeeze = detect_bb_squeeze(df)
    if squeeze:
        result["bb_squeeze"] = squeeze

    return result
