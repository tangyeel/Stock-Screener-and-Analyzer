"""
screener/sector_check.py — Sector-level trend cross-check (v2 spec §6.3).

Before a candidate is finalised, its sector index must not be in a
confirmed downtrend. This catches rotation — Pharma strength during
an IT-led index drawdown is preserved; a stock whose own sector is
broken is excluded even if the stock itself briefly passes other filters.

The check: sector index close > sector SMA50.
"""

import json
import uuid
import logging
from datetime import datetime

from db.database import get_connection
from data.sector_data import get_sector_for_symbol

logger = logging.getLogger(__name__)


def sector_trend_ok(sector_name: str) -> bool:
    """
    Check whether a sector index is in an uptrend.

    Condition: sector index latest close > its SMA50.

    Args:
        sector_name: e.g. 'NIFTY IT', 'NIFTY PHARMA'

    Returns:
        True if trend is OK or sector data is unavailable (fail-open
        for unknown sectors — don't block a stock because of missing data).
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT close, sma50 FROM sector_index_prices
            WHERE sector=? ORDER BY date DESC LIMIT 1
            """,
            (sector_name,),
        ).fetchone()

    if row is None:
        logger.warning("No sector data for %s — allowing pass to avoid false block.", sector_name)
        return True

    close = row["close"]
    sma50 = row["sma50"]

    if close is None or sma50 is None or sma50 <= 0:
        logger.warning("Incomplete sector data for %s — allowing pass.", sector_name)
        return True

    ok = float(close) > float(sma50)
    if not ok:
        logger.info("Sector %s is below SMA50 — candidates in this sector may be blocked.", sector_name)

    return ok


def run_sector_check(
    candidates: list[dict],
    run_id: str,
) -> list[dict]:
    """
    Apply the sector trend check to all remaining candidates.

    Filters out any candidate whose sector index is below its SMA50.
    Logs every check to filter_log with stage='sector_check'.

    Args:
        candidates: list of candidate dicts with 'symbol' key.
        run_id:     current run UUID.

    Returns:
        Filtered list with only sector-OK candidates.
    """
    passed = []
    for row in candidates:
        symbol = row.get("symbol", "?")
        sector = get_sector_for_symbol(symbol)

        if sector is None:
            logger.debug("No sector mapping for %s — skipping sector check.", symbol)
            _log_sector_check(run_id, symbol, True, {"reason": "no_sector_mapping"})
            passed.append(row)
            continue

        ok = sector_trend_ok(sector)
        _log_sector_check(run_id, symbol, ok, {"sector": sector, "trend_ok": ok})

        if ok:
            passed.append(row)
        else:
            logger.info("Sector check blocked %s — %s below SMA50.", symbol, sector)

    return passed


def _log_sector_check(run_id: str, symbol: str, passed: bool, details: dict) -> None:
    from db.database import accumulate_filter_log
    accumulate_filter_log(run_id, symbol, "sector_check", passed, details)
