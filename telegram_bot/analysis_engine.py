"""
Technical Analysis Engine — 5-category multi-factor analysis for equities & indices.

Each category independently scores signals and returns a verdict.
The composite verdict weights all categories per config.

Reuses screener DB for Nifty100 stocks when cached indicators exist.
"""

import logging
from datetime import date

import pandas as pd
import numpy as np

from config import CATEGORY_WEIGHTS, SECTOR_NAME_MAP
from data.yfinance_fetcher import fetch_yfinance
from db.database import get_connection

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _categorize(score: float) -> str:
    if score >= 0.65:
        return "Bullish"
    if score >= 0.40:
        return "Neutral"
    return "Bearish"


def _safe(val, default=0.0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return float(val)


def _is_universe_stock(ticker: str) -> bool:
    """Check if ticker is in the screener universe (currently Nifty 500)."""
    with get_connection() as conn:
        r = conn.execute(
            "SELECT 1 FROM nifty100_constituents WHERE symbol=? AND is_active=1",
            (ticker,),
        ).fetchone()
    return r is not None


def _latest_screener_date() -> str | None:
    with get_connection() as conn:
        r = conn.execute(
            "SELECT MAX(dp.date) as md FROM daily_prices dp "
            "INNER JOIN daily_indicators di ON di.symbol=dp.symbol AND di.date=dp.date"
        ).fetchone()
    return r["md"] if r and r["md"] else None


def _load_screener_row(ticker: str, d: str) -> dict | None:
    with get_connection() as conn:
        r = conn.execute(
            """SELECT di.*, dp.close, dp.volume
               FROM daily_indicators di
               JOIN daily_prices dp ON dp.symbol=di.symbol AND dp.date=di.date
               WHERE di.symbol=? AND di.date=?""",
            (ticker, d),
        ).fetchone()
    return dict(r) if r else None


# ── Data fetching ─────────────────────────────────────────────────────────────

def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_index()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    df["sma20"] = close.rolling(20).mean()
    df["sma50"] = close.rolling(50).mean()
    df["sma150"] = close.rolling(150).mean()
    df["sma200"] = close.rolling(200).mean()
    df["sma200_slope"] = df["sma200"].diff(5)

    # ATR14
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # RSI14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    # ADX14
    plus_dm = high.diff().clip(lower=0)
    minus_dm = low.diff().clip(upper=0).abs()
    tr14 = tr.rolling(14).mean()
    pdi = 100 * plus_dm.rolling(14).mean() / tr14.replace(0, np.nan)
    ndi = 100 * minus_dm.rolling(14).mean() / tr14.replace(0, np.nan)
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx14"] = dx.rolling(14).mean()

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Stochastics
    ll = low.rolling(14).min()
    hh = high.rolling(14).max()
    df["stoch_k"] = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ROC10
    df["roc10"] = close.pct_change(10) * 100

    # OBV
    obv = (volume * ((close.diff() > 0).astype(int) * 2 - 1)).cumsum()
    df["obv"] = obv
    df["obv_slope"] = obv.rolling(20).apply(lambda x: (x.iloc[-1] - x.iloc[0]) / 20, raw=False)

    # Bollinger Bands
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std

    # Avg volume
    df["avg_vol_20"] = volume.rolling(20).mean()

    # Week 52 high/low
    df["week52_high"] = close.rolling(252).max()
    df["week52_low"] = close.rolling(252).min()

    df["close"] = close
    return df


def fetch_data(ticker: str) -> pd.DataFrame | None:
    try:
        df = fetch_yfinance(ticker, lookback_days=300)
        if df is None or df.empty or len(df) < 200:
            return None
        return _compute_indicators(df)
    except Exception as e:
        logger.warning("Failed to fetch data for %s: %s", ticker, e)
        return None


# ── Category: Trend ───────────────────────────────────────────────────────────

def analyze_trend(d: pd.Series, w: pd.Series | None = None) -> dict:
    signals = {
        "price_above_sma20":     _safe(d.get("close")) > _safe(d.get("sma20")),
        "price_above_sma50":     _safe(d.get("close")) > _safe(d.get("sma50")),
        "price_above_sma150":    _safe(d.get("close")) > _safe(d.get("sma150")),
        "price_above_sma200":    _safe(d.get("close")) > _safe(d.get("sma200")),
        "ma_stack_bullish":      _safe(d.get("sma50")) > _safe(d.get("sma150")) > _safe(d.get("sma200")),
        "sma200_rising":         _safe(d.get("sma200_slope")) > 0,
        "adx_trending":          _safe(d.get("adx14")) > 25,
    }
    if w is not None:
        signals["weekly_trend_up"] = _safe(w.get("close")) > _safe(w.get("sma20"))
    else:
        signals["weekly_trend_up"] = True
    bullish = sum(1 for v in signals.values() if v)
    total = len(signals)
    score = bullish / total if total else 0
    return {"signals": signals, "score": round(score, 2), "verdict": _categorize(score)}


# ── Category: Momentum ────────────────────────────────────────────────────────

def _detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    recent = df.iloc[-lookback:]
    price_hh = recent["close"].iloc[-1] == recent["close"].max()
    rsi_lh = recent["rsi14"].iloc[-1] < recent["rsi14"].max()
    bearish = bool(price_hh and rsi_lh)
    price_ll = recent["close"].iloc[-1] == recent["close"].min()
    rsi_hl = recent["rsi14"].iloc[-1] > recent["rsi14"].min()
    bullish = bool(price_ll and rsi_hl)
    return {"bearish": bearish, "bullish": bullish}


def analyze_momentum(d: pd.Series, df: pd.DataFrame) -> dict:
    macd_line = _safe(d.get("macd"))
    macd_signal = _safe(d.get("macd_signal"))
    macd_hist = _safe(d.get("macd_hist"))
    rsi = _safe(d.get("rsi14"))
    signals = {
        "rsi_bullish_zone":      50 <= rsi <= 70,
        "rsi_not_overbought":    rsi < 80,
        "rsi_not_oversold":      rsi > 20,
        "macd_above_signal":     macd_line > macd_signal,
        "macd_histogram_rising": macd_hist > _safe(df.iloc[-2].get("macd_hist")) if len(df) >= 2 else True,
        "stoch_bullish":         _safe(d.get("stoch_k")) > _safe(d.get("stoch_d")),
        "roc_positive":          _safe(d.get("roc10")) > 0,
    }
    div = _detect_rsi_divergence(df)
    signals["no_bearish_divergence"] = not div["bearish"]
    bullish = sum(1 for v in signals.values() if v)
    total = len(signals)
    score = bullish / total if total else 0
    return {
        "signals": signals,
        "divergence_flag": div,
        "score": round(score, 2),
        "verdict": _categorize(score),
    }


# ── Category: Volume & Accumulation ───────────────────────────────────────────

def _detect_accumulation(df: pd.DataFrame) -> bool:
    recent = df.iloc[-20:]
    up_vol = recent[recent["close"].diff() > 0]["volume"].sum() if len(recent) > 1 else 0
    down_vol = recent[recent["close"].diff() <= 0]["volume"].sum() if len(recent) > 1 else 0
    return up_vol > down_vol if down_vol else False


def analyze_volume(d: pd.Series, df: pd.DataFrame) -> dict:
    signals = {
        "volume_above_avg":   _safe(d.get("volume")) > _safe(d.get("avg_vol_20")),
        "obv_rising":         _safe(d.get("obv_slope")) > 0,
        "accumulation_pattern": _detect_accumulation(df),
    }
    delivery = d.get("delivery_pct_slope")
    if delivery is not None:
        signals["delivery_pct_rising"] = _safe(delivery) > 0
    bullish = sum(1 for v in signals.values() if v)
    total = len(signals)
    score = bullish / total if total else 0
    return {"signals": signals, "score": round(score, 2), "verdict": _categorize(score)}


# ── Category: Volatility & Structure ──────────────────────────────────────────

def _find_nearest_support(df: pd.DataFrame, lookback: int = 50) -> float:
    if df.empty or "low" not in df.columns:
        return df["close"].iloc[-1] * 0.9 if not df.empty and "close" in df.columns else 0
    recent = df.iloc[-lookback:]
    # Simple: nearest swing low in last 50 days
    lows = recent["low"].values
    close = recent["close"].iloc[-1]
    below = [l for l in lows if l < close]
    return max(below) if below else close * 0.9


def analyze_volatility_structure(d: pd.Series, df: pd.DataFrame) -> dict:
    bb_lower = _safe(d.get("bb_lower"))
    bb_upper = _safe(d.get("bb_upper"))
    close = _safe(d.get("close"))
    bb_range = bb_upper - bb_lower
    bb_position = (close - bb_lower) / bb_range if bb_range > 0 else 0.5

    signals = {
        "not_extended_from_bb": 0.2 <= bb_position <= 0.9,
        "atr_pct_reasonable":   (_safe(d.get("atr14")) / close) < 0.05 if close else False,
        "near_52w_high":        close >= 0.85 * _safe(d.get("week52_high")) if _safe(d.get("week52_high")) else False,
        "above_key_support":    close > _find_nearest_support(df),
    }
    bullish = sum(1 for v in signals.values() if v)
    total = len(signals)
    score = bullish / total if total else 0
    return {
        "signals": signals,
        "bb_position": round(bb_position, 2),
        "score": round(score, 2),
        "verdict": _categorize(score),
    }


# ── Category: Relative Strength ───────────────────────────────────────────────

def analyze_relative_strength(rs_rating: float | None, sector_rs: float | None) -> dict:
    rs = _safe(rs_rating, 50)
    srs = _safe(sector_rs, 50)
    signals = {
        "rs_above_median":   rs >= 50,
        "rs_strong":         rs >= 70,
        "sector_also_strong": srs >= 50,
    }
    bullish = sum(1 for v in signals.values() if v)
    total = len(signals)
    score = bullish / total if total else 0
    return {
        "rs_rating": int(rs),
        "sector_rs_rating": int(srs),
        "signals": signals,
        "score": round(score, 2),
        "verdict": _categorize(score),
    }


# ── Screener DB reuse ─────────────────────────────────────────────────────────

def _try_screener_data(ticker: str, run_date: str) -> dict | None:
    row = _load_screener_row(ticker, run_date)
    if not row:
        return None

    d = pd.Series({
        "close": row["close"],
        "volume": row["volume"],
        "sma20": row["sma20"],
        "sma50": row["sma50"],
        "sma150": row["sma150"],
        "sma200": row["sma200"],
        "sma200_slope": row["sma200_slope"],
        "atr14": row["atr14"],
        "rsi14": row["rsi14"],
        "avg_vol_20": row["avg_vol_20"],
        "week52_high": row["week52_high"],
        "week52_low": row["week52_low"],
        "delivery_pct_slope": row["delivery_pct_slope"],
        "adx14": None,
        "macd": None,
        "macd_signal": None,
        "macd_hist": None,
        "stoch_k": None,
        "stoch_d": None,
        "roc10": None,
        "obv": None,
        "obv_slope": None,
        "bb_upper": None,
        "bb_lower": None,
    })
    rs = row.get("rs_rating")
    sector_rs = row.get("sector_rs_rating")

    results = {
        "trend": analyze_trend(d),
        "volatility_structure": analyze_volatility_structure(d, pd.DataFrame()),
        "relative_strength": analyze_relative_strength(rs, sector_rs),
    }
    # Momentum and volume need full df — mark as unknown
    results["momentum"] = {"signals": {}, "divergence_flag": {"bearish": False, "bullish": False},
                           "score": 0.5, "verdict": "Neutral", "note": "Cached — full data unavailable"}
    results["volume"] = {"signals": {}, "score": 0.5, "verdict": "Neutral",
                         "note": "Cached — full data unavailable"}
    return results


# ── Composite verdict ─────────────────────────────────────────────────────────

def synthesize(category_results: dict) -> dict:
    score = sum(
        category_results[cat]["score"] * CATEGORY_WEIGHTS[cat]
        for cat in CATEGORY_WEIGHTS if cat in category_results
    )
    score = round(score, 2)
    if score >= 0.70:
        label = "Bullish"
    elif score >= 0.45:
        label = "Neutral"
    else:
        label = "Bearish"
    breakdown = {}
    for cat in category_results:
        r = category_results[cat]
        breakdown[cat] = {"score": r["score"], "verdict": r["verdict"]}
    return {"composite_score": score, "verdict": label, "category_breakdown": breakdown}


# ── Trade setup ────────────────────────────────────────────────────────────────

def _build_trade_setup(d: pd.Series, df: pd.DataFrame, levels: dict) -> dict | None:
    if not levels:
        return None
    close = _safe(d.get("close"))
    support = levels.get("nearest_support")
    resistance = levels.get("nearest_resistance")
    atr = _safe(d.get("atr14"))
    if not close or not atr or atr <= 0:
        return None

    setup = {}
    # Entry zone near support
    if support and support < close:
        setup["entry_zone"] = f"₹{support:.0f}–₹{close:.0f}"
        setup["entry_note"] = f"Near support at ₹{support:.0f}"
        stop = round(support - atr * 0.5, 2)
        setup["stop_loss"] = f"₹{stop:.0f}"
        risk = close - stop
        if risk > 0:
            if resistance and resistance > close:
                target = resistance
                rr = (target - close) / risk
                setup["target"] = f"₹{target:.0f}"
                setup["risk_reward"] = f"1:{rr:.1f}"
            setup["risk_pct"] = round(risk / close * 100, 1)
    else:
        entry = round(close, 2)
        setup["entry_zone"] = f"₹{entry:.0f}"
        stop = round(entry - 1.5 * atr, 2)
        setup["stop_loss"] = f"₹{stop:.0f}"
        risk = entry - stop
        if risk > 0 and resistance and resistance > entry:
            rr = (resistance - entry) / risk
            setup["target"] = f"₹{resistance:.0f}"
            setup["risk_reward"] = f"1:{rr:.1f}"
        setup["risk_pct"] = round(risk / entry * 100, 1)

    return setup if setup.get("risk_reward") else None


# ── Main entry ────────────────────────────────────────────────────────────────

def run_analysis(ticker: str, sector: str | None = None) -> dict:
    # Always fetch earnings + fundamentals (lightweight, works independently)
    from telegram_bot.earnings import check_earnings
    from telegram_bot.fundamentals import check as check_fundamentals
    extra = {}
    earnings = check_earnings(ticker)
    if earnings:
        extra["earnings"] = earnings
    fundamentals = check_fundamentals(ticker, sector)
    if fundamentals:
        extra["fundamentals"] = fundamentals

    # 1. Try screener DB path
    latest = _latest_screener_date()
    if latest and _is_universe_stock(ticker):
        cached = _try_screener_data(ticker, latest)
        if cached:
            result = synthesize(cached)
            result["category_results"] = cached
            result["source"] = "screener_db"
            result["run_date"] = latest
            result.update(extra)
            return result

    # 2. Fresh yfinance path
    df = fetch_data(ticker)
    if df is None or len(df) < 200:
        result = {"error": "Insufficient price data for analysis"}
        result.update(extra)
        return result

    d = df.iloc[-1]
    w = None
    if len(df) >= 5:
        try:
            weekly = df.resample("W-FRI").agg({
                "close": "last", "high": "max", "low": "min", "volume": "sum",
            })
            w = weekly.iloc[-1] if len(weekly) >= 2 else None
        except Exception:
            pass

    results = {
        "trend": analyze_trend(d, w),
        "momentum": analyze_momentum(d, df),
        "volume": analyze_volume(d, df),
        "volatility_structure": analyze_volatility_structure(d, df),
        "relative_strength": analyze_relative_strength(None, None),
    }
    result = synthesize(results)
    result["category_results"] = results
    result["source"] = "yfinance"
    result["run_date"] = str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1])

    # Enrich with fresh-data features
    from telegram_bot.levels import calc_all as calc_levels
    from telegram_bot.patterns import detect_all as detect_patterns

    levels = calc_levels(df)
    if levels:
        result["levels"] = levels

    pattern = detect_patterns(df)
    if pattern:
        result["pattern"] = pattern

    setup = _build_trade_setup(d, df, levels or {})
    if setup:
        result["trade_setup"] = setup

    result.update(extra)
    return result
