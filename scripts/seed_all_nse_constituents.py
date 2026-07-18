"""
scripts/seed_all_nse_constituents.py — Seed all active listed NSE equities into SQLite.

Downloads the official list of listed equities directly from NSE India.

Usage:
    python scripts/seed_all_nse_constituents.py
    python scripts/seed_all_nse_constituents.py --replace
"""

import sys
import csv
import io
import logging
import argparse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_connection, init_db

logger = logging.getLogger(__name__)

# URL for all listed equities on NSE
EQUITY_LIST_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"


def seed(replace: bool = False) -> None:
    init_db()

    # User-agent header to avoid being blocked by NSE
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        logger.info("Downloading all listed equities list from NSE...")
        req = urllib.request.Request(EQUITY_LIST_URL, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        raw_data = resp.read().decode("utf-8-sig")  # Decode with BOM if present
        
        # Parse CSV
        reader = csv.reader(io.StringIO(raw_data))
        headers = [h.strip() for h in next(reader)]
        
        rows = []
        for row in reader:
            if not row:
                continue
            rows.append(dict(zip(headers, row)))
            
    except Exception as e:
        logger.error("Failed to download listed equities list: %s", e)
        return

    if not rows:
        logger.error("Listed equities list is empty — nothing to seed.")
        return

    logger.info("Downloaded %d rows from listed equities list", len(rows))

    # We only want EQ (Equity) series to filter out debt/mutual fund instruments
    # Some symbols also have BE (Book Entry) series, but EQ is the standard liquid series.
    valid_series = {"EQ"}

    with get_connection() as conn:
        if replace:
            conn.execute("UPDATE nifty100_constituents SET is_active=0")
            logger.info("Deactivated existing constituents for replace.")

        inserted = 0
        skipped = 0
        today = date.today().isoformat()

        for row in rows:
            symbol = row.get("SYMBOL", "").strip().upper()
            company = row.get("NAME OF COMPANY", "").strip()
            series = row.get("SERIES", "").strip().upper()
            
            # Skip if series is not EQ
            if series not in valid_series:
                skipped += 1
                continue

            if not symbol:
                skipped += 1
                continue

            try:
                conn.execute(
                    """
                    INSERT INTO nifty100_constituents
                        (symbol, company_name, sector, added_date, is_active)
                    VALUES (?, ?, 'Unknown', ?, 1)
                    ON CONFLICT(symbol) DO UPDATE SET
                        company_name = excluded.company_name,
                        is_active    = 1,
                        removed_date = NULL
                    """,
                    (symbol, company, today),
                )
                inserted += 1
            except Exception as e:
                logger.warning("Skipped %s: %s", symbol, e)
                skipped += 1

    logger.info("Seeded %d active equities (%d skipped).", inserted, skipped)
    print(f"Done — {inserted} symbols loaded, {skipped} skipped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    parser = argparse.ArgumentParser(description="Seed all active NSE equities into SQLite from NSE.")
    parser.add_argument("--replace", action="store_true", help="Clear and re-insert all rows")
    args = parser.parse_args()

    seed(replace=args.replace)
