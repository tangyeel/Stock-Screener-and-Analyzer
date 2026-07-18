"""
Populate instrument_master with mutual funds from AMFI's daily NAV file.

Downloads from:
    https://www.amfiindia.com/spages/NAVAll.txt

Run periodically to keep the fund list current.
"""

import uuid
import logging
from io import StringIO

import requests
import pandas as pd

logger = logging.getLogger(__name__)

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"


def fetch_nav_text(retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(AMFI_NAV_URL, timeout=120)
            resp.raise_for_status()
            text = resp.text
            if len(text) < 1000:
                raise ValueError(f"Response too short ({len(text)} chars)")
            return text
        except Exception as e:
            logger.warning("AMFI download attempt %d/%d failed: %s", attempt + 1, retries + 1, e)
            if attempt < retries:
                import time
                time.sleep(5)
    raise RuntimeError("AMFI download failed after all retries")


def parse_nav_text(text: str) -> list[dict]:
    funds = []
    current_category = None
    current_scheme_type = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith("Open Ended Schemes"):
            current_scheme_type = line
            current_category = None
            continue
        if line.startswith("Scheme Code") or line.startswith("Scheme"):
            continue
        parts = line.split(";")
        # Detect category header (e.g. "Equity Scheme" or just a single word after scheme data)
        if len(parts) == 1 and len(line) < 80 and not line[0].isdigit():
            current_category = line
            continue
        if len(parts) >= 6:
            scheme_code = parts[0].strip()
            scheme_name = parts[3].strip()
            nav = parts[4].strip() if len(parts) > 4 else ""
            if scheme_code and scheme_name and scheme_code.isdigit():
                funds.append({
                    "scheme_code": scheme_code,
                    "scheme_name": scheme_name,
                    "category": current_category or current_scheme_type or "Unknown",
                })
        if (i + 1) % 5000 == 0:
            logger.info("Parsed %d lines... (%d funds found)", i + 1, len(funds))
    return funds


def populate_funds(funds: list[dict] = None):
    from db.database import get_connection
    if funds is None:
        logger.info("Downloading AMFI NAV file...")
        text = fetch_nav_text()
        funds = parse_nav_text(text)
        logger.info("Parsed %d fund schemes", len(funds))

    with get_connection() as conn:
        count = 0
        for f in funds:
            name_lower = f["scheme_name"].lower()
            aliases = list({name_lower})
            words = name_lower.replace(" - ", " ").split()
            if len(words) > 1:
                aliases.append(" ".join(words[:4]))
                if len(words) >= 3:
                    aliases.append(" ".join(words[:3]))
            aliases_str = ",".join(aliases)
            sector = f.get("category", "")
            conn.execute(
                """INSERT OR IGNORE INTO instrument_master
                   (id, instrument_type, ticker, primary_name, aliases, sector, exchange, is_active)
                   VALUES (?, 'mutual_fund', ?, ?, ?, ?, 'AMFI', 1)""",
                (str(uuid.uuid4()), f["scheme_code"], f["scheme_name"],
                 aliases_str, sector),
            )
            count += 1
    logger.info("Populated %d mutual funds", count)
    print(f"Populated {count} mutual funds")


if __name__ == "__main__":
    populate_funds()
