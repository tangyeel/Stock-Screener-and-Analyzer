"""
data/sector_data.py — Sector index and India VIX data fetching.

Fetches EOD data for Nifty sector indices and India VIX from yfinance.
Stores sector index OHLCV + SMAs in sector_index_prices table.
VIX is returned as a float for breadth/regime computation.
"""

import uuid
import logging
from datetime import date

import pandas as pd

from config import SECTOR_TICKERS, INDIA_VIX_TICKER, LOOKBACK_DAYS
from db.database import get_connection
from data.yfinance_fetcher import fetch_index_history

logger = logging.getLogger(__name__)


def fetch_sector_index_data(run_id: str, lookback_days: int = LOOKBACK_DAYS) -> dict[str, pd.DataFrame]:
    """
    Fetch EOD history for all configured Nifty sector indices.

    Computes SMA50 and SMA200 for each sector and stores in
    sector_index_prices. Handles errors per-sector so one failure
    doesn't block the entire pipeline.

    Returns:
        {sector_name: DataFrame} with columns [date, close].
    """
    results: dict[str, pd.DataFrame] = {}

    for sector_name, ticker in SECTOR_TICKERS.items():
        try:
            df = fetch_index_history(ticker, lookback_days)
            df = df.sort_values("date").reset_index(drop=True)
            closes = pd.to_numeric(df["close"], errors="coerce")

            sma50  = closes.rolling(50).mean()
            sma200 = closes.rolling(200).mean()

            latest = df.iloc[-1]
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sector_index_prices
                        (sector, date, close, sma50, sma200)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        sector_name,
                        str(latest["date"]),
                        float(closes.iloc[-1]),
                        float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else None,
                        float(sma200.iloc[-1]) if pd.notna(sma200.iloc[-1]) else None,
                    ),
                )

            results[sector_name] = df
            logger.info(
                "Sector %s: %d rows, close=%.2f",
                sector_name, len(df), closes.iloc[-1],
            )
        except Exception as e:
            logger.warning("Failed to fetch sector %s (%s): %s", sector_name, ticker, e)

    return results


def fetch_vix_value() -> float | None:
    """
    Fetch the latest available India VIX closing value.

    Returns:
        VIX close as float, or None if fetch/parse fails.
    """
    try:
        df = fetch_index_history(INDIA_VIX_TICKER, lookback_days=10)
        closes = pd.to_numeric(df["close"], errors="coerce").dropna()
        if closes.empty:
            logger.warning("VIX data is empty.")
            return None
        vix = float(closes.iloc[-1])
        logger.info("India VIX: %.2f", vix)
        return vix
    except Exception as e:
        logger.warning("Failed to fetch India VIX: %s", e)
        return None


def get_sector_for_symbol(symbol: str) -> str | None:
    """
    Look up the sector for a given Nifty 100 symbol.

    Returns the mapped Nifty sector index name (e.g. 'NIFTY IT')
    so it can be used directly as a key in SECTOR_TICKERS and sector_dfs.
    Returns None if the symbol or sector mapping is not found.
    """
    from config import SECTOR_NAME_MAP

    with get_connection() as conn:
        row = conn.execute(
            "SELECT sector FROM nifty100_constituents WHERE symbol=? AND is_active=1",
            (symbol,),
        ).fetchone()

    if row is None:
        return None

    raw_sector = row["sector"]
    return SECTOR_NAME_MAP.get(raw_sector, raw_sector)


def get_sector_latest_df(sector_name: str) -> pd.DataFrame | None:
    """
    Load the full price history for a sector index from the DB.

    Used by sector RS rating computation.
    Returns DataFrame with [date, close] or None.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date, close FROM sector_index_prices WHERE sector=? ORDER BY date ASC",
            (sector_name,),
        ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df
