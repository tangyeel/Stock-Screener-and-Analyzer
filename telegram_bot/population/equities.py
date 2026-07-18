"""
Populate instrument_master with equities.

Sources (in priority order):
  1. Existing nifty100_constituents table → mapped directly (has sector info)
  2. NSE equity list download (full market) → all 2,385 NSE symbols
"""

import csv
import io
import logging
import urllib.request
import uuid

logger = logging.getLogger(__name__)

NSE_EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"


def _generate_aliases(ticker: str, company_name: str) -> list[str]:
    aliases = {ticker.lower(), company_name.lower()}
    cleaned = company_name.lower().replace(" limited", "").replace(" ltd", "").replace(".", "").strip()
    aliases.add(cleaned)
    words = cleaned.split()
    if len(words) > 1:
        acronym = "".join(w[0] for w in words if w).lower()
        aliases.add(acronym)
    return sorted(aliases)


def populate_from_nifty100():
    """Upsert Nifty 500 constituents into instrument_master with sector info."""
    from db.database import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol, company_name, sector FROM nifty100_constituents WHERE is_active=1"
        ).fetchall()
        count = 0
        for r in rows:
            aliases = _generate_aliases(r["symbol"], r["company_name"] or r["symbol"])
            aliases_str = ",".join(aliases)
            conn.execute(
                """INSERT INTO instrument_master
                   (id, instrument_type, ticker, primary_name, aliases, sector, exchange, is_active)
                   VALUES (?, 'equity', ?, ?, ?, ?, 'NSE', 1)
                   ON CONFLICT(ticker, instrument_type) DO UPDATE SET
                       sector = excluded.sector,
                       primary_name = excluded.primary_name,
                       aliases = excluded.aliases,
                       exchange = excluded.exchange,
                       is_active = 1""",
                (str(uuid.uuid4()), r["symbol"], r["company_name"] or r["symbol"],
                 aliases_str, r["sector"]),
            )
            count += 1
    logger.info("Upserted %d equities from Nifty 500", count)
    return count


def populate_from_nse_list():
    """Download full NSE equity list and add any symbols not already in instrument_master."""
    from db.database import get_connection

    try:
        logger.info("Downloading NSE equity list from %s …", NSE_EQUITY_URL)
        resp = urllib.request.urlopen(NSE_EQUITY_URL, timeout=15)
        reader = csv.DictReader(io.StringIO(resp.read().decode()))
    except Exception as e:
        logger.warning("Failed to download NSE equity list: %s", e)
        return 0

    with get_connection() as conn:
        existing = {r["ticker"].lower() for r in conn.execute(
            "SELECT ticker FROM instrument_master WHERE instrument_type='equity'"
        ).fetchall()}

        count = 0
        for row in reader:
            ticker = row.get("SYMBOL", "").strip()
            name = row.get("NAME OF COMPANY", "").strip()
            if not ticker or ticker.lower() in existing:
                continue

            aliases = _generate_aliases(ticker, name or ticker)
            aliases_str = ",".join(aliases)
            conn.execute(
                """INSERT OR IGNORE INTO instrument_master
                   (id, instrument_type, ticker, primary_name, aliases, sector, exchange, is_active)
                   VALUES (?, 'equity', ?, ?, ?, NULL, 'NSE', 1)""",
                (str(uuid.uuid4()), ticker, name or ticker, aliases_str),
            )
            count += 1

    logger.info("Populated %d equities from NSE full list", count)
    return count


def populate_equities():
    nf = populate_from_nifty100()
    ns = populate_from_nse_list()
    total = nf + ns
    print(f"Populated {total} equities ({nf} from Nifty 100, {ns} from NSE list)")


if __name__ == "__main__":
    populate_equities()
