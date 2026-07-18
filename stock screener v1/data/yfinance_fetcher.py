"""
data/yfinance_fetcher.py — Fetch OHLCV history via yfinance as fallback.

yfinance appends ".NS" to NSE symbols (e.g., RELIANCE → RELIANCE.NS).
Returns a DataFrame matching the daily_prices schema.
"""

import logging
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# Symbols where yfinance uses a different ticker than the NSE symbol.
# Update this whenever a symbol is renamed or Yahoo Finance breaks a ticker.
_TICKER_OVERRIDES: dict[str, str] = {
    # ZOMATO was relisted as ETERNAL on NSE; Yahoo still hasn't updated ZOMATO.NS
    "ZOMATO": "ETERNAL.NS",
}


def _to_yf_ticker(symbol: str) -> str:
    """Convert NSE symbol to yfinance ticker format."""
    symbol = symbol.strip().upper()
    if symbol.endswith(".NS"):
        return symbol
    if symbol in _TICKER_OVERRIDES:
        return _TICKER_OVERRIDES[symbol]
    return f"{symbol}.NS"


def fetch_yfinance(
    symbol: str,
    lookback_days: int = 260,
    start_date: str | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV history for a symbol via yfinance.

    Args:
        symbol:        NSE symbol, e.g. 'RELIANCE' or 'BAJAJ-AUTO'
        lookback_days: Target number of trading rows to keep (ignored when
                       start_date is provided for incremental fetches).
        start_date:    ISO date string; fetch from this date onward (inclusive).
                       When set, the response is NOT trimmed to lookback_days,
                       so the caller can incrementally append new rows.

    Returns:
        DataFrame with columns: symbol, date, open, high, low, close, volume,
                                delivery_pct.  Sorted ascending by date.

    Raises:
        ValueError if yfinance returns empty data.
    """
    ticker = _to_yf_ticker(symbol)
    end = date.today()

    if start_date is not None:
        start = start_date
        logger.debug("yfinance incremental fetch: %s from %s to %s", ticker, start, end)
    else:
        calendar_days = int(lookback_days * 1.5)
        start = (end - timedelta(days=calendar_days)).isoformat()
        logger.debug("yfinance full fetch: %s from %s to %s", ticker, start, end)

    raw: pd.DataFrame = yf.download(
        ticker,
        start=start,
        end=end.isoformat(),
        auto_adjust=True,   # adjusts for splits and dividends
        progress=False,
        threads=False,
    )

    if raw.empty:
        raise ValueError(f"yfinance returned no data for {ticker}")

    # yfinance MultiIndex columns when downloading single ticker
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    raw = raw.reset_index()
    raw = raw.rename(columns={"Date": "date", "index": "date"})
    raw["date"] = pd.to_datetime(raw["date"]).dt.date.astype(str)
    raw["symbol"] = symbol.strip().upper()
    raw["delivery_pct"] = None  # yfinance doesn't provide delivery data

    # Normalise column names
    col_renames = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    for src, dst in col_renames.items():
        if src not in raw.columns and src.capitalize() in raw.columns:
            raw = raw.rename(columns={src.capitalize(): dst})

    required = ["symbol", "date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError(f"yfinance response missing columns: {missing} for {ticker}")

    df = raw[required + ["delivery_pct"]].copy()
    df = df.sort_values("date").reset_index(drop=True)

    # Drop rows where close is NaN (can happen on partial days or data gaps)
    df = df.dropna(subset=["close"])

    # Trim to requested lookback ONLY on full fetches (not incremental)
    if start_date is None and len(df) > lookback_days:
        df = df.tail(lookback_days).reset_index(drop=True)

    logger.info("yfinance fetched %d rows for %s", len(df), symbol)
    return df


def fetch_index_history(ticker: str = "^CNX100", lookback_days: int = 260) -> pd.DataFrame:
    """
    Fetch index OHLCV history (used for RS Rating and regime check).
    Same logic as fetch_yfinance but without the .NS suffix.

    Args:
        ticker:        yfinance index ticker, e.g. '^CNX100' (Nifty 100)
        lookback_days: Number of trading days

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    calendar_days = int(lookback_days * 1.5)
    end = date.today()
    start = end - timedelta(days=calendar_days)

    logger.debug("yfinance index fetch: %s from %s to %s", ticker, start, end)

    raw: pd.DataFrame = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if raw.empty:
        raise ValueError(f"yfinance returned no data for index {ticker}")

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0].lower() for col in raw.columns]
    else:
        raw.columns = [c.lower() for c in raw.columns]

    raw = raw.reset_index()
    raw["date"] = pd.to_datetime(raw["Date"]).dt.date.astype(str)
    raw = raw.drop(columns=["Date"], errors="ignore")

    df = raw[["date", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("date").dropna(subset=["close"]).reset_index(drop=True)

    if len(df) > lookback_days:
        df = df.tail(lookback_days).reset_index(drop=True)

    logger.info("yfinance index fetched %d rows for %s", len(df), ticker)
    return df
