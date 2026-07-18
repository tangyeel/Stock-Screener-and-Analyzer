"""
data/nse_bhavcopy.py — Download and parse NSE EOD Bhavcopy CSVs.

NSE publishes Bhavcopy (end-of-day data) at:
  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<DDMMYYYY>_F_0000.csv.zip

This module fetches one day's Bhavcopy, filters to equity segment,
and returns a clean DataFrame matching the daily_prices schema.

NOTE: NSE can rate-limit or change URLs. The yfinance fallback in
ingestion.py handles any fetch failure automatically.
"""

import io
import logging
import zipfile
from datetime import date, timedelta

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# NSE Bhavcopy URL pattern (CM = Capital Market / equity segment)
_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com",
    "Accept-Language": "en-US,en;q=0.9",
}

_TIMEOUT = 30  # seconds


def _date_to_str(d: date) -> str:
    """Format date as DDMMYYYY for NSE URL."""
    return d.strftime("%d%m%Y")


def fetch_bhavcopy_day(target_date: date) -> pd.DataFrame:
    """
    Download and parse the NSE Bhavcopy for a single trading day.

    Returns a DataFrame with columns:
        symbol, date, open, high, low, close, volume, delivery_pct

    Raises requests.HTTPError or ValueError on any failure so the
    caller (ingestion.py) can fall back to yfinance.
    """
    date_str = _date_to_str(target_date)
    url = _BHAVCOPY_URL.format(date_str=date_str)
    logger.debug("Fetching Bhavcopy from %s", url)

    # Use a session with NSE headers to avoid 401/403
    session = requests.Session()
    # Warm up the session with NSE homepage cookie
    try:
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)
    except Exception:
        pass  # Best-effort cookie fetch; proceed anyway

    resp = session.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()

    # Unzip in-memory
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(csv_name) as f:
            raw = pd.read_csv(f)

    return _parse_bhavcopy(raw, target_date)


def _parse_bhavcopy(raw: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """
    Clean and normalise a raw Bhavcopy DataFrame.
    NSE column names vary slightly across years; this handles the common variants.
    """
    # Normalise column names
    raw.columns = [c.strip().upper() for c in raw.columns]

    # Keep only EQ (equity) series — drops BE, SM, etc.
    if "SERIES" in raw.columns:
        raw = raw[raw["SERIES"].str.strip() == "EQ"].copy()

    # Map known column name variants → our schema
    col_map = {
        # symbol
        "SYMBOL": "symbol",
        "TckrSymb": "symbol",
        # open
        "OPEN": "open", "OPENPRICE": "open", "OPEN_PRICE": "open",
        # high
        "HIGH": "high", "HIGHPRICE": "high", "HIGH_PRICE": "high",
        # low
        "LOW": "low", "LOWPRICE": "low", "LOW_PRICE": "low",
        # close
        "CLOSE": "close", "CLOSEPRICE": "close", "CLOSE_PRICE": "close",
        # volume
        "TOTTRDQTY": "volume", "VOLUME": "volume", "TTL_TRD_QNTY": "volume",
        # delivery (optional — not always in Bhavcopy; filled later from delivery report)
        "DELIV_QTY": "delivery_qty",
        "DLYQTY": "delivery_qty",
    }
    raw = raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns})

    required = {"symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Bhavcopy missing expected columns: {missing}")

    df = raw[list(required | {"delivery_qty"} & set(raw.columns))].copy()
    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["date"] = target_date.isoformat()

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

    # Compute delivery_pct if raw delivery qty is available
    if "delivery_qty" in df.columns:
        df["delivery_qty"] = pd.to_numeric(df["delivery_qty"], errors="coerce").fillna(0)
        df["delivery_pct"] = (df["delivery_qty"] / df["volume"].replace(0, pd.NA)) * 100
    else:
        df["delivery_pct"] = None

    df = df.drop(columns=["delivery_qty"], errors="ignore")
    df = df.dropna(subset=["close"])

    logger.info(
        "Bhavcopy parsed for %s: %d equity records",
        target_date.isoformat(),
        len(df),
    )
    return df[["symbol", "date", "open", "high", "low", "close", "volume", "delivery_pct"]]


def get_symbol_history_from_bhavcopy(
    symbol: str, lookback_days: int = 260
) -> pd.DataFrame:
    """
    Attempt to build a price history by fetching multiple Bhavcopy days.
    This is expensive (one HTTP request per trading day) — only called if
    the symbol isn't already in daily_prices with enough history.

    In practice, yfinance is preferred for bulk history. This function
    is a last-resort for very recent symbols not yet on yfinance.
    """
    frames = []
    today = date.today()
    attempts = 0
    max_attempts = lookback_days * 2  # allow for weekends/holidays

    d = today - timedelta(days=1)
    while len(frames) < lookback_days and attempts < max_attempts:
        if d.weekday() < 5:  # skip weekends (rough filter)
            try:
                day_df = fetch_bhavcopy_day(d)
                sym_row = day_df[day_df["symbol"] == symbol.upper()]
                if not sym_row.empty:
                    frames.append(sym_row)
            except Exception as e:
                logger.debug("No Bhavcopy for %s on %s: %s", symbol, d, e)
        d -= timedelta(days=1)
        attempts += 1

    if not frames:
        raise ValueError(f"No Bhavcopy history found for {symbol}")

    result = pd.concat(frames, ignore_index=True).sort_values("date")
    logger.info("Built %d-day Bhavcopy history for %s", len(result), symbol)
    return result
