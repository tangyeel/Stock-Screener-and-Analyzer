"""
data/ingestion.py — Ingestion orchestrator.

Fetches EOD price history for all active Nifty 100 constituents.
Primary source: NSE Bhavcopy (today's full market CSV).
Fallback: yfinance (per-symbol history fetch).

Writes new rows to daily_prices and logs every symbol's outcome
to ingestion_log. Skips symbols that already have today's data.
"""

import time
import uuid
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from config import LOOKBACK_DAYS, NIFTY100_TICKER
from db.database import get_connection
from data.nse_bhavcopy import fetch_bhavcopy_day
from data.yfinance_fetcher import fetch_yfinance, fetch_index_history

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_active_symbols() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol FROM nifty100_constituents WHERE is_active=1 ORDER BY symbol"
        ).fetchall()
    return [r["symbol"] for r in rows]


def _already_ingested(symbol: str, target_date: str) -> bool:
    """Return True if we already have a row for this symbol+date."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_prices WHERE symbol=? AND date=?",
            (symbol, target_date),
        ).fetchone()
    return row is not None


def _count_existing_rows(symbol: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM daily_prices WHERE symbol=?", (symbol,)
        ).fetchone()
    return row["cnt"] if row else 0


def _latest_price_date(symbol: str) -> str | None:
    """Return the most recent date in daily_prices for a symbol."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) as md FROM daily_prices WHERE symbol=?", (symbol,)
        ).fetchone()
    return row["md"] if row and row["md"] else None


def _upsert_prices(df: pd.DataFrame) -> int:
    """Insert rows into daily_prices, ignoring duplicates. Returns count inserted."""
    rows = df.to_dict(orient="records")
    inserted = 0
    with get_connection() as conn:
        for r in rows:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO daily_prices
                        (symbol, date, open, high, low, close, volume, delivery_pct, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["symbol"], r["date"],
                        r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                        r.get("volume"), r.get("delivery_pct"), r.get("source"),
                    ),
                )
                inserted += 1
            except Exception as e:
                logger.warning("Failed to insert %s/%s: %s", r.get("symbol"), r.get("date"), e)
    return inserted


def _log_ingestion(run_id: str, symbol: str, status: str,
                   source: str = None, rows_fetched: int = 0, error: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ingestion_log (id, run_id, symbol, status, source, rows_fetched, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (str(uuid.uuid4()), run_id, symbol, status, source, rows_fetched, error),
        )


# ── Core fetch with fallback ───────────────────────────────────────────────────

def fetch_eod_data(symbol: str, run_id: str, lookback_days: int = LOOKBACK_DAYS,
                   start_date: str | None = None) -> tuple[pd.DataFrame, str]:
    """
    Fetch EOD price history for a single symbol.
    Retries once with backoff on transient yfinance errors.

    When start_date is provided, fetches only rows from that date onward
    (incremental update).  Otherwise fetches up to lookback_days of history.

    Returns (DataFrame, source_used).
    Raises on total failure — caller should catch and log.
    """
    last_err = None
    for attempt in range(2):
        try:
            df = fetch_yfinance(symbol, lookback_days, start_date)
            df["source"] = "yfinance"
            _log_ingestion(run_id, symbol, "success", source="yfinance", rows_fetched=len(df))
            return df, "yfinance"
        except Exception as yf_err:
            last_err = yf_err
            if attempt == 0:
                logger.warning("yfinance failed for %s (attempt 1): %s — retrying…", symbol, yf_err)
                time.sleep(2)
            else:
                logger.warning("yfinance failed for %s (attempt 2): %s — no fallback available", symbol, yf_err)

    _log_ingestion(run_id, symbol, "failed", error=str(last_err))
    raise last_err


# ── Today's Bhavcopy enrichment ────────────────────────────────────────────────

def enrich_with_todays_bhavcopy(symbols: list[str], run_id: str, target_date: date) -> dict[str, bool]:
    """
    Try to fetch today's Bhavcopy and upsert the latest day's OHLCV for all symbols.
    This supplements yfinance (which may lag by a day for very recent data).

    Returns a dict {symbol: enriched_bool}.
    """
    enriched = {s: False for s in symbols}
    try:
        day_df = fetch_bhavcopy_day(target_date)
        day_df["source"] = "nse_bhavcopy"
        nifty_symbols = set(s.upper() for s in symbols)
        relevant = day_df[day_df["symbol"].isin(nifty_symbols)].copy()

        if relevant.empty:
            logger.warning("Bhavcopy had no rows matching our universe for %s", target_date)
            return enriched

        _upsert_prices(relevant)
        for sym in relevant["symbol"].tolist():
            enriched[sym] = True
        logger.info(
            "Bhavcopy enriched %d/%d symbols for %s",
            len(relevant), len(symbols), target_date,
        )
    except Exception as e:
        logger.warning("Bhavcopy enrichment failed: %s — will rely on yfinance only", e)

    return enriched


# ── Main ingestion run ─────────────────────────────────────────────────────────

def run_ingestion(run_id: str) -> dict:
    """
    Full ingestion pass for all active Nifty 100 constituents.

    Steps:
    1. Load active symbols from DB
    2. For each symbol, fetch history via yfinance if we don't have enough rows
    3. Enrich latest day from NSE Bhavcopy where available
    4. Return summary stats for run_log

    Returns:
        {
            "stocks_ingested": int,
            "nse_primary_count": int,
            "yfinance_count": int,
            "failed_count": int,
            "data_source_primary_pct": float,
        }
    """
    symbols = _get_active_symbols()
    if not symbols:
        raise RuntimeError("No active constituents found — run seed_constituents.py first.")

    today = date.today()
    today_str = today.isoformat()

    success_count = 0
    yf_count = 0
    failed_count = 0

    # Bhavcopy enrichment FIRST — covers all symbols in one go if available
    enrich_with_todays_bhavcopy(symbols, run_id, today)

    # Check how many symbols need a full history fetch
    symbols_needing_backfill = [s for s in symbols if _count_existing_rows(s) < 200]
    skip_yfinance_backfill = len(symbols_needing_backfill) > 20
    if skip_yfinance_backfill:
        logger.warning(
            "Found %d symbols needing EOD price history backfill. "
            "Bypassing sequential yfinance downloads to avoid rate limiting. "
            "Please run: python scripts/backfill_prices.py",
            len(symbols_needing_backfill)
        )

    for symbol in symbols:
        existing_rows = _count_existing_rows(symbol)

        # Already has price data → skip (bhavcopy handles today's data when available)
        # Threshold: any symbol with >= 200 rows has enough for indicator computation
        if existing_rows >= 200:
            logger.debug("Skipping %s — already have %d rows.", symbol, existing_rows)
            success_count += 1
            continue

        if skip_yfinance_backfill:
            logger.debug("Skipping yfinance backfill for %s due to bulk skip safeguard.", symbol)
            failed_count += 1
            continue

        try:
            # Incremental: fetch only missing days if symbol already has data
            last_date = _latest_price_date(symbol)
            if last_date and last_date < today_str and existing_rows >= 200:
                next_day = (
                    datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
                logger.debug(
                    "Incremental fetch for %s from %s (have %d rows)",
                    symbol, next_day, existing_rows,
                )
                df, source = fetch_eod_data(symbol, run_id, start_date=next_day)
            else:
                # Full fetch: new symbol or too few rows
                logger.debug(
                    "Full fetch for %s (have %d rows)", symbol, existing_rows,
                )
                df, source = fetch_eod_data(symbol, run_id)

            _upsert_prices(df)
            success_count += 1
            if source == "yfinance":
                yf_count += 1
        except Exception as e:
            logger.error("Ingestion failed for %s: %s", symbol, e)
            failed_count += 1

    nse_primary_count = success_count - yf_count
    total = success_count + failed_count
    primary_pct = round((nse_primary_count / total * 100) if total else 0, 1)

    logger.info(
        "Ingestion complete — %d succeeded (%d via yfinance), %d failed",
        success_count, yf_count, failed_count,
    )

    return {
        "stocks_ingested": success_count,
        "nse_primary_count": nse_primary_count,
        "yfinance_count": yf_count,
        "failed_count": failed_count,
        "data_source_primary_pct": primary_pct,
    }


def fetch_index_data(run_id: str, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    Fetch Nifty 100 index history for RS Rating and regime computation.
    Not stored in daily_prices — returned as a DataFrame for in-memory use.
    """
    try:
        df = fetch_index_history(ticker=NIFTY100_TICKER, lookback_days=lookback_days)
        logger.info("Index data fetched: %d rows", len(df))
        return df
    except Exception as e:
        _log_ingestion(run_id, "__INDEX__", "failed", error=str(e))
        raise RuntimeError(f"Failed to fetch index data: {e}") from e
