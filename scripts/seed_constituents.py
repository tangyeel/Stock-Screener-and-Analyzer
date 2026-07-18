"""
scripts/seed_constituents.py — Load Nifty 500 constituents from NSE into SQLite.

Downloads the latest Nifty 500 list directly from NSE India.

Usage:
    python scripts/seed_constituents.py
    python scripts/seed_constituents.py --replace   # clears and re-inserts all rows
    python scripts/seed_constituents.py --list      # show current constituents
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

NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"


def seed(replace: bool = False) -> None:
    init_db()

    try:
        logger.info("Downloading Nifty 500 list from NSE …")
        resp = urllib.request.urlopen(NIFTY500_URL, timeout=15)
        reader = csv.DictReader(io.StringIO(resp.read().decode()))
        rows = list(reader)
    except Exception as e:
        logger.error("Failed to download Nifty 500 list: %s", e)
        return

    if not rows:
        logger.error("Nifty 500 list is empty — nothing to seed.")
        return

    logger.info("Downloaded %d symbols from Nifty 500 list", len(rows))

    with get_connection() as conn:
        if replace:
            conn.execute("DELETE FROM nifty100_constituents")
            logger.info("Cleared existing constituents for full replace.")

        inserted = 0
        skipped = 0
        today = date.today().isoformat()

        for row in rows:
            symbol = row["Symbol"].strip().upper()
            company = row.get("Company Name", "").strip()
            sector = row.get("Industry", "").strip()
            if not symbol:
                skipped += 1
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO nifty100_constituents
                        (symbol, company_name, sector, added_date, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(symbol) DO UPDATE SET
                        company_name = excluded.company_name,
                        sector       = excluded.sector,
                        is_active    = 1,
                        removed_date = NULL
                    """,
                    (symbol, company, sector, today),
                )
                inserted += 1
            except Exception as e:
                logger.warning("Skipped %s: %s", symbol, e)
                skipped += 1

    logger.info("Seeded %d constituents (%d skipped).", inserted, skipped)
    print(f"Done — {inserted} symbols loaded, {skipped} skipped.")


def list_constituents() -> None:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, sector FROM nifty100_constituents WHERE is_active=1 ORDER BY symbol"
        ).fetchall()
    print(f"\nActive constituents ({len(rows)} symbols):\n")
    for r in rows:
        print(f"  {r['symbol']:<20} {r['sector']:<45} {r['company_name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Nifty 500 constituents into SQLite from NSE.")
    parser.add_argument("--replace", action="store_true", help="Clear and re-insert all rows")
    parser.add_argument("--list", action="store_true", help="List current active constituents")
    args = parser.parse_args()

    if args.list:
        list_constituents()
    else:
        seed(replace=args.replace)
