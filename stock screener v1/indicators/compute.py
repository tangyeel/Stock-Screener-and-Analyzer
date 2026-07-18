"""
indicators/compute.py — Compute all technical indicators for a single symbol.

Uses the `ta` library (pip install ta) for MA, ATR, RSI.
Computes additional derived indicators in plain pandas.

Output is written to daily_indicators table.
Call compute_all_indicators(run_date) to process all symbols for a given day.
"""

import logging
import math
from datetime import date

import pandas as pd
import ta.trend as ta_trend
import ta.volatility as ta_vol
import ta.momentum as ta_mom

from db.database import get_connection

logger = logging.getLogger(__name__)

# Minimum rows needed to produce meaningful 200-period indicators
MIN_ROWS_REQUIRED = 210


def compute_indicators(df: pd.DataFrame) -> pd.Series | None:
    """
    Compute all indicators for a single symbol's price history DataFrame.

    Args:
        df: DataFrame with columns [date, open, high, low, close, volume, delivery_pct]
            sorted ascending by date. Must have at least MIN_ROWS_REQUIRED rows.

    Returns:
        pd.Series with all indicator values for the MOST RECENT row.
        Returns None if insufficient data.
    """
    if len(df) < MIN_ROWS_REQUIRED:
        logger.debug(
            "Skipping indicators — only %d rows (need %d)", len(df), MIN_ROWS_REQUIRED
        )
        return None

    df = df.copy().sort_values("date").reset_index(drop=True)

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # ── Moving Averages ──────────────────────────────────────────────────────
    df["sma20"]  = ta_trend.sma_indicator(close, window=20)
    df["sma50"]  = ta_trend.sma_indicator(close, window=50)
    df["sma150"] = ta_trend.sma_indicator(close, window=150)
    df["sma200"] = ta_trend.sma_indicator(close, window=200)

    # SMA200 slope: 21-day difference (~1 month) — positive means trending up
    df["sma200_slope"] = df["sma200"].diff(21)

    # ── Volatility & Momentum ─────────────────────────────────────────────────
    df["atr14"] = ta_vol.AverageTrueRange(
        high=high, low=low, close=close, window=14
    ).average_true_range()

    df["rsi14"] = ta_mom.RSIIndicator(close=close, window=14).rsi()

    # ── Volume ───────────────────────────────────────────────────────────────
    df["avg_vol_20"] = volume.rolling(20).mean()

    # ── 52-Week High/Low (252 trading days) ───────────────────────────────────
    df["week52_high"] = close.rolling(252, min_periods=200).max()
    df["week52_low"]  = close.rolling(252, min_periods=200).min()

    # ── Consolidation Tightness ───────────────────────────────────────────────
    # Coefficient of variation over last 20 days — lower = tighter base
    df["consolidation_tightness"] = (
        close.rolling(20).std() / close.rolling(20).mean()
    )

    # ── Delivery Slope ────────────────────────────────────────────────────────
    if "delivery_pct" in df.columns and df["delivery_pct"].notna().sum() > 10:
        df["delivery_pct_slope"] = (
            df["delivery_pct"].rolling(5).mean().diff(5)
        )
    else:
        df["delivery_pct_slope"] = 0.0

    # Return indicators for the most recent row only
    latest = df.iloc[-1]

    return pd.Series({
        "sma20":                   _safe(latest, "sma20"),
        "sma50":                   _safe(latest, "sma50"),
        "sma150":                  _safe(latest, "sma150"),
        "sma200":                  _safe(latest, "sma200"),
        "sma200_slope":            _safe(latest, "sma200_slope"),
        "atr14":                   _safe(latest, "atr14"),
        "rsi14":                   _safe(latest, "rsi14"),
        "avg_vol_20":              _safe(latest, "avg_vol_20"),
        "week52_high":             _safe(latest, "week52_high"),
        "week52_low":              _safe(latest, "week52_low"),
        "consolidation_tightness": _safe(latest, "consolidation_tightness"),
        "delivery_pct_slope":      _safe(latest, "delivery_pct_slope"),
        # rs_rating is filled in separately by rs_rating.py after all symbols processed
        "rs_rating":               None,
    })


def _safe(row: pd.Series, col: str):
    """Return float or None (avoid NaN in DB)."""
    val = row.get(col)
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


def load_price_history(symbol: str, min_rows: int = MIN_ROWS_REQUIRED) -> pd.DataFrame | None:
    """Load price history for a symbol from daily_prices."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume, delivery_pct
            FROM daily_prices
            WHERE symbol = ?
            ORDER BY date ASC
            """,
            (symbol,),
        ).fetchall()

    if not rows:
        return None

    df = pd.DataFrame([dict(r) for r in rows])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")

    if len(df) < min_rows:
        logger.debug("%s: only %d rows in DB (need %d)", symbol, len(df), min_rows)
        return None

    return df


def upsert_indicators(symbol: str, run_date: str, indicators: pd.Series) -> None:
    """Write a single symbol's indicators to daily_indicators (upsert)."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO daily_indicators (
                symbol, date, sma20, sma50, sma150, sma200, sma200_slope,
                atr14, rsi14, avg_vol_20, week52_high, week52_low,
                consolidation_tightness, delivery_pct_slope, rs_rating
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                sma20                   = excluded.sma20,
                sma50                   = excluded.sma50,
                sma150                  = excluded.sma150,
                sma200                  = excluded.sma200,
                sma200_slope            = excluded.sma200_slope,
                atr14                   = excluded.atr14,
                rsi14                   = excluded.rsi14,
                avg_vol_20              = excluded.avg_vol_20,
                week52_high             = excluded.week52_high,
                week52_low              = excluded.week52_low,
                consolidation_tightness = excluded.consolidation_tightness,
                delivery_pct_slope      = excluded.delivery_pct_slope,
                rs_rating               = COALESCE(excluded.rs_rating, daily_indicators.rs_rating)
            """,
            (
                symbol, run_date,
                indicators.get("sma20"),        indicators.get("sma50"),
                indicators.get("sma150"),       indicators.get("sma200"),
                indicators.get("sma200_slope"), indicators.get("atr14"),
                indicators.get("rsi14"),        indicators.get("avg_vol_20"),
                indicators.get("week52_high"),  indicators.get("week52_low"),
                indicators.get("consolidation_tightness"),
                indicators.get("delivery_pct_slope"),
                indicators.get("rs_rating"),
            ),
        )


def compute_all_indicators(run_date: str = None) -> dict:
    """
    Compute indicators for all active constituents in bulk.
    Writes results to daily_indicators.

    Args:
        run_date: ISO date string (e.g. '2026-07-11'). Defaults to today.

    Returns:
        {"computed": int, "skipped": int}
    """
    if run_date is None:
        run_date = date.today().isoformat()

    with get_connection() as conn:
        active_symbols = [
            r["symbol"]
            for r in conn.execute(
                "SELECT symbol FROM nifty100_constituents WHERE is_active=1 ORDER BY symbol"
            ).fetchall()
        ]
        
        logger.info("Loading price histories in bulk...")
        df_all = pd.read_sql_query(
            """SELECT symbol, date, open, high, low, close, volume, delivery_pct 
               FROM daily_prices 
               WHERE symbol IN (SELECT symbol FROM nifty100_constituents WHERE is_active=1)
               ORDER BY symbol, date ASC""",
            conn
        )

    grouped = df_all.groupby("symbol")
    
    computed = 0
    skipped  = 0
    
    insert_data = []

    for symbol in active_symbols:
        if symbol not in grouped.groups:
            logger.debug("No price history for %s — skipping.", symbol)
            skipped += 1
            continue

        df_sym = grouped.get_group(symbol)
        
        if len(df_sym) < MIN_ROWS_REQUIRED:
            logger.debug("%s: only %d rows in DB (need %d)", symbol, len(df_sym), MIN_ROWS_REQUIRED)
            skipped += 1
            continue

        indicators = compute_indicators(df_sym)
        if indicators is None:
            skipped += 1
            continue

        insert_data.append((
            symbol, run_date,
            indicators.get("sma20"),        indicators.get("sma50"),
            indicators.get("sma150"),       indicators.get("sma200"),
            indicators.get("sma200_slope"), indicators.get("atr14"),
            indicators.get("rsi14"),        indicators.get("avg_vol_20"),
            indicators.get("week52_high"),  indicators.get("week52_low"),
            indicators.get("consolidation_tightness"),
            indicators.get("delivery_pct_slope"),
            indicators.get("rs_rating"),
        ))
        computed += 1

    if insert_data:
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO daily_indicators (
                    symbol, date, sma20, sma50, sma150, sma200, sma200_slope,
                    atr14, rsi14, avg_vol_20, week52_high, week52_low,
                    consolidation_tightness, delivery_pct_slope, rs_rating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    sma20                   = excluded.sma20,
                    sma50                   = excluded.sma50,
                    sma150                  = excluded.sma150,
                    sma200                  = excluded.sma200,
                    sma200_slope            = excluded.sma200_slope,
                    atr14                   = excluded.atr14,
                    rsi14                   = excluded.rsi14,
                    avg_vol_20              = excluded.avg_vol_20,
                    week52_high             = excluded.week52_high,
                    week52_low              = excluded.week52_low,
                    consolidation_tightness = excluded.consolidation_tightness,
                    delivery_pct_slope      = excluded.delivery_pct_slope,
                    rs_rating               = COALESCE(excluded.rs_rating, daily_indicators.rs_rating)
                """,
                insert_data
            )

    logger.info(
        "Indicator computation complete for %s — %d computed, %d skipped",
        run_date, computed, skipped,
    )
    return {"computed": computed, "skipped": skipped}
