"""
data/fetch_prices.py — Ensure all Nifty 100 price data is up-to-date.

Auto-detects missing dates and downloads only what's needed.
Skips today (market may still be open) — only fetches completed days.
Safe to run daily — skips symbols that already have full history.

Usage:
    python -m data.fetch_prices
"""

import logging
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import init_db, get_connection
from data.ingestion import run_ingestion

logger = logging.getLogger(__name__)


def run():
    init_db()

    # Seed constituents if empty
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM nifty100_constituents"
        ).fetchone()
    if not row or row["c"] == 0:
        logger.info("nifty100_constituents is empty — seeding…")
        try:
            from scripts.seed_constituents import main as seed_main
            seed_main()
        except Exception as e:
            logger.error("Auto-seed failed: %s. Run: python scripts/seed_constituents.py", e)
            return False

    # Check latest date we already have
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) as md FROM daily_prices").fetchone()
    latest = row["md"] if row and row["md"] else None

    # Fetch up to yesterday (today's data may not be available yet)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if latest and latest >= yesterday:
        logger.info("Data already up-to-date through %s. Nothing to fetch.", latest)
        return True

    logger.info("Latest data: %s — fetching missing data through %s…", latest or "none", yesterday)
    run_ingestion("auto_fetch")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run()
