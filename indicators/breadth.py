"""
indicators/breadth.py — Market breadth and regime-input computation.

Computes breadth metrics from the daily_indicators table:
  - % of Nifty 100 stocks above SMA50
  - % of Nifty 100 stocks above SMA200

Writes results to market_breadth_daily along with the index data
needed for regime tiering.
"""

import logging
from datetime import date

import pandas as pd

from db.database import get_connection
from config import NIFTY100_TICKER

logger = logging.getLogger(__name__)


def calc_market_breadth(run_date: str = None, index_df: pd.DataFrame = None,
                        india_vix: float = None) -> dict:
    """
    Compute market breadth for a given date.

    Reads all indicators for the date from daily_indicators,
    calculates the % of stocks whose close > SMA50 and close > SMA200.

    Args:
        run_date:  ISO date string. Defaults to today.
        index_df:  Optional Nifty 100 index DataFrame (for index close/SMAs).
        india_vix: Optional India VIX value for storage.

    Returns:
        {
            "pct_above_sma50":  float,
            "pct_above_sma200": float,
            "india_vix":        float or None,
            "nifty100_close":   float or None,
            "nifty100_sma50":   float or None,
            "nifty100_sma200":  float or None,
        }
    """
    if run_date is None:
        run_date = date.today().isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT dp.close, di.sma50, di.sma200
            FROM daily_indicators di
            JOIN daily_prices dp
                ON di.symbol = dp.symbol AND di.date = dp.date
            JOIN nifty100_constituents c ON c.symbol = di.symbol
            WHERE di.date = ? AND c.is_active = 1
            """,
            (run_date,),
        ).fetchall()

    if not rows:
        logger.warning("No indicator rows found for %s — cannot compute breadth.", run_date)
        return {
            "pct_above_sma50": 0.0,
            "pct_above_sma200": 0.0,
            "india_vix": india_vix,
            "nifty100_close": None,
            "nifty100_sma50": None,
            "nifty100_sma200": None,
        }

    df = pd.DataFrame([dict(r) for r in rows])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["sma50"] = pd.to_numeric(df["sma50"], errors="coerce")
    df["sma200"] = pd.to_numeric(df["sma200"], errors="coerce")

    above_sma50  = ((df["close"] > df["sma50"]) & df["sma50"].notna()).mean() * 100
    above_sma200 = ((df["close"] > df["sma200"]) & df["sma200"].notna()).mean() * 100

    result = {
        "pct_above_sma50":  round(float(above_sma50), 1),
        "pct_above_sma200": round(float(above_sma200), 1),
        "india_vix":        india_vix,
        "nifty100_close":   None,
        "nifty100_sma50":   None,
        "nifty100_sma200":  None,
    }

    if index_df is not None and not index_df.empty:
        idx_closes = pd.to_numeric(index_df["close"], errors="coerce").dropna()
        if len(idx_closes) >= 200:
            idx_sma50  = idx_closes.rolling(50).mean().iloc[-1]
            idx_sma200 = idx_closes.rolling(200).mean().iloc[-1]
            result["nifty100_close"]  = round(float(idx_closes.iloc[-1]), 2)
            result["nifty100_sma50"]  = round(float(idx_sma50), 2) if pd.notna(idx_sma50) else None
            result["nifty100_sma200"] = round(float(idx_sma200), 2) if pd.notna(idx_sma200) else None

    _store_breadth(run_date, result)

    logger.info(
        "Market breadth: %.1f%% above SMA50, %.1f%% above SMA200",
        result["pct_above_sma50"], result["pct_above_sma200"],
    )

    return result


def _store_breadth(run_date: str, data: dict) -> None:
    """Write breadth data to market_breadth_daily."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO market_breadth_daily
                (date, pct_above_sma50, pct_above_sma200,
                 india_vix, nifty100_close, nifty100_sma50, nifty100_sma200)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                data["pct_above_sma50"],
                data["pct_above_sma200"],
                data.get("india_vix"),
                data["nifty100_close"],
                data["nifty100_sma50"],
                data["nifty100_sma200"],
            ),
        )
