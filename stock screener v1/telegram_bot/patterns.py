"""
Candlestick pattern detection — last 2-3 candles.

Detects:
  - Bullish / Bearish Engulfing
  - Hammer / Hanging Man
  - Shooting Star / Inverted Hammer
  - Doji
  - Morning Star / Evening Star (3-candle)
  - Piercing Line / Dark Cloud Cover
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _body_len(c: pd.Series) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def _wick_upper(c: pd.Series) -> float:
    return float(c["high"]) - max(float(c["close"]), float(c["open"]))


def _wick_lower(c: pd.Series) -> float:
    return min(float(c["close"]), float(c["open"])) - float(c["low"])


def _total_range(c: pd.Series) -> float:
    return float(c["high"]) - float(c["low"])


def _is_bullish(c: pd.Series) -> bool:
    return float(c["close"]) > float(c["open"])


def _is_bearish(c: pd.Series) -> bool:
    return float(c["close"]) < float(c["open"])


def detect_engulfing(c1: pd.Series, c2: pd.Series) -> str | None:
    """Bullish: bear candle followed by larger bullish candle that engulfs it."""
    if _is_bearish(c1) and _is_bullish(c2):
        if (float(c2["close"]) > float(c1["open"])
                and float(c2["open"]) < float(c1["close"])):
            return "bullish_engulfing"
    if _is_bullish(c1) and _is_bearish(c2):
        if (float(c2["close"]) < float(c1["open"])
                and float(c2["open"]) > float(c1["close"])):
            return "bearish_engulfing"
    return None


def detect_hammer(c: pd.Series) -> str | None:
    """Small body, long lower wick (>=2x body), little/no upper wick."""
    body = _body_len(c)
    tr = _total_range(c)
    if tr == 0 or body / tr > 0.3:
        return None
    upper = _wick_upper(c)
    lower = _wick_lower(c)
    if lower >= 2 * body and upper <= 0.3 * body:
        return "hammer" if _is_bullish(c) else "hanging_man"
    return None


def detect_shooting_star(c: pd.Series) -> str | None:
    """Small body, long upper wick, little/no lower wick."""
    body = _body_len(c)
    tr = _total_range(c)
    if tr == 0 or body / tr > 0.3:
        return None
    upper = _wick_upper(c)
    lower = _wick_lower(c)
    if upper >= 2 * body and lower <= 0.3 * body:
        return "shooting_star" if _is_bearish(c) else "inverted_hammer"
    return None


def detect_doji(c: pd.Series) -> str | None:
    """Body very small relative to range."""
    tr = _total_range(c)
    body = _body_len(c)
    if tr > 0 and body / tr < 0.1:
        return "doji"
    return None


def detect_morning_evening_star(c0: pd.Series, c1: pd.Series, c2: pd.Series) -> str | None:
    """Morning: bear → small → bull. Evening: bull → small → bear."""
    body1 = _body_len(c1)
    tr1 = _total_range(c1)
    if tr1 == 0 or body1 / tr1 > 0.3:
        return None  # middle candle must be small
    if _is_bearish(c0) and _is_bullish(c2):
        if (float(c2["close"]) > (float(c0["open"]) + float(c0["close"])) / 2):
            return "morning_star"
    if _is_bullish(c0) and _is_bearish(c2):
        if (float(c2["close"]) < (float(c0["open"]) + float(c0["close"])) / 2):
            return "evening_star"
    return None


def detect_piercing(c1: pd.Series, c2: pd.Series) -> str | None:
    """Piercing: bear candle → bullish candle closing > midpoint of bear's body."""
    if _is_bearish(c1) and _is_bullish(c2):
        mid = (float(c1["open"]) + float(c1["close"])) / 2
        if float(c2["close"]) > mid and float(c2["open"]) < float(c1["close"]):
            return "piercing_line"
    return None


def detect_dark_cloud(c1: pd.Series, c2: pd.Series) -> str | None:
    """Dark cloud: bullish candle → bearish candle closing < midpoint of bull's body."""
    if _is_bullish(c1) and _is_bearish(c2):
        mid = (float(c1["open"]) + float(c1["close"])) / 2
        if float(c2["close"]) < mid and float(c2["open"]) > float(c1["close"]):
            return "dark_cloud_cover"
    return None


PATTERN_SIGNAL = {
    "bullish_engulfing": (+2, "Bullish engulfing — strong buying pressure"),
    "bearish_engulfing": (-2, "Bearish engulfing — strong selling pressure"),
    "hammer": (+2, "Hammer — potential reversal up near support"),
    "hanging_man": (-2, "Hanging man — potential reversal down near resistance"),
    "shooting_star": (-2, "Shooting star — rejection at highs"),
    "inverted_hammer": (+2, "Inverted hammer — potential bullish reversal"),
    "doji": (0, "Doji — indecision, wait for confirmation"),
    "morning_star": (+3, "Morning star — strong bullish reversal signal"),
    "evening_star": (-3, "Evening star — strong bearish reversal signal"),
    "piercing_line": (+2, "Piercing line — bullish reversal"),
    "dark_cloud_cover": (-2, "Dark cloud cover — bearish reversal"),
}


def detect_all(df: pd.DataFrame) -> dict | None:
    if len(df) < 3:
        return None

    c0 = df.iloc[-3]
    c1 = df.iloc[-2]
    c2 = df.iloc[-1]

    patterns = []

    # 2-candle patterns
    for pair in [(c1, c2)]:
        p = detect_engulfing(pair[0], pair[1])
        if p:
            patterns.append(p)
        p = detect_piercing(pair[0], pair[1])
        if p:
            patterns.append(p)
        p = detect_dark_cloud(pair[0], pair[1])
        if p:
            patterns.append(p)

    # 1-candle patterns
    for c in [c2]:
        p = detect_hammer(c)
        if p:
            patterns.append(p)
        p = detect_shooting_star(c)
        if p:
            patterns.append(p)
        p = detect_doji(c)
        if p:
            patterns.append(p)

    # 3-candle patterns
    p = detect_morning_evening_star(c0, c1, c2)
    if p:
        patterns.append(p)

    if not patterns:
        return None

    best = max(patterns, key=lambda x: abs(PATTERN_SIGNAL.get(x, (0, ""))[0]))
    signal, description = PATTERN_SIGNAL.get(best, (0, ""))
    return {
        "patterns": patterns,
        "primary": best,
        "signal": signal,
        "description": description,
    }
