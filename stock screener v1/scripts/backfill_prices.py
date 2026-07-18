"""
scripts/backfill_prices.py — Bulk backfill of price data for NSE symbols.
Downloads history for all symbols with zero rows in daily_prices in chunks of 50.
"""
import sys
import os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent) if 'Path' in globals() else '.')

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import init_db, get_connection

logger = logging.getLogger(__name__)

# Override tickers (same as yfinance_fetcher.py)
_TICKER_OVERRIDES = {"ZOMATO": "ETERNAL.NS"}

def _to_yf(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith(".NS"):
        return symbol
    if symbol in _TICKER_OVERRIDES:
        return _TICKER_OVERRIDES[symbol]
    return f"{symbol}.NS"

def backfill():
    init_db()
    with get_connection() as conn:
        symbols = [
            r["symbol"] for r in conn.execute(
                "SELECT symbol FROM nifty100_constituents WHERE is_active=1 ORDER BY symbol"
            ).fetchall()
        ]

    # Find symbols with zero rows
    empty = []
    with get_connection() as conn:
        for sym in symbols:
            cnt = conn.execute("SELECT COUNT(*) as c FROM daily_prices WHERE symbol=?", (sym,)).fetchone()
            if cnt["c"] == 0:
                empty.append(sym)

    if not empty:
        logger.info("No empty symbols found — all data backfilled.")
        return

    logger.info("Backfilling %d symbols in chunks of 50 via yfinance download...", len(empty))

    end = date.today()
    start = (end - timedelta(days=400)).isoformat()
    
    chunk_size = 50
    inserted = 0
    failed = 0
    
    for i in range(0, len(empty), chunk_size):
        chunk_symbols = empty[i:i+chunk_size]
        chunk_tickers = [_to_yf(s) for s in chunk_symbols]
        logger.info(
            "Downloading chunk %d/%d (%d symbols)...", 
            (i // chunk_size) + 1, 
            ((len(empty) - 1) // chunk_size) + 1, 
            len(chunk_symbols)
        )
        
        try:
            raw = yf.download(
                chunk_tickers,
                start=start,
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            logger.error("Failed to download chunk starting with %s: %s", chunk_symbols[0], e)
            failed += len(chunk_symbols)
            continue
            
        if raw.empty:
            logger.warning("Empty data returned for chunk starting with %s", chunk_symbols[0])
            failed += len(chunk_symbols)
            continue
            
        with get_connection() as conn:
            for sym in chunk_symbols:
                yft = _to_yf(sym)
                try:
                    # Parse based on structure
                    if isinstance(raw.columns, pd.MultiIndex):
                        # MultiIndex: check if ticker is in level 0 or 1
                        if yft in raw.columns.get_level_values(0):
                            sym_df = raw[yft].copy()
                        elif yft in raw.columns.get_level_values(1):
                            sym_df = raw.xs(yft, axis=1, level=1).copy()
                        else:
                            failed += 1
                            continue
                    else:
                        sym_df = raw.copy()
                    
                    sym_df = sym_df.reset_index()
                    sym_df = sym_df.rename(columns={
                        "Date": "date", "Open": "open", "High": "high",
                        "Low": "low", "Close": "close", "Volume": "volume",
                        "open": "open", "high": "high", "low": "low",
                        "close": "close", "volume": "volume",
                    })
                    sym_df["symbol"] = sym
                    sym_df["delivery_pct"] = None
                    sym_df["date"] = pd.to_datetime(sym_df["date"]).dt.date.astype(str)
                    
                    required = ["symbol", "date", "open", "high", "low", "close", "volume"]
                    for c in required:
                        if c not in sym_df.columns:
                            sym_df[c] = None
                    sym_df = sym_df[required + ["delivery_pct"]].dropna(subset=["close"])
                    
                    for _, row in sym_df.iterrows():
                        conn.execute(
                            """INSERT OR IGNORE INTO daily_prices
                               (symbol, date, open, high, low, close, volume, delivery_pct, source)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yfinance')""",
                            (sym, row["date"], row["open"], row["high"],
                             row["low"], row["close"], row["volume"], row["delivery_pct"]),
                        )
                    inserted += 1
                except Exception as e:
                    logger.debug("Failed to parse %s: %s", sym, e)
                    failed += 1
                    
        logger.info("Progress: %d symbols successfully processed, %d failed", inserted, failed)

    logger.info("Backfill complete: %d symbols successfully inserted, %d failed", inserted, failed)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    backfill()
