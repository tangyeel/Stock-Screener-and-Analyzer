"""
jobs/track_outcomes.py — EOD Outcome Tracker.

Run this job once daily around 4:00 PM IST (after market close)
to check how open picks are performing against the day's price action.

It initialises pending outcome rows for new picks and updates
existing rows based on whether entry was triggered, stop was hit,
or target was reached.

Usage:
    python jobs/track_outcomes.py
    python jobs/track_outcomes.py --date 2026-07-11   # backfill a specific date

This is what eventually lets you compute the system's real hit rate,
average R:R achieved, and expectancy — essential before risking capital.
"""

import sys
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_connection, init_db
from data.yfinance_fetcher import fetch_yfinance

logger = logging.getLogger(__name__)

# A pick expires (neither triggered, stopped, nor hit target) after this many days
EXPIRY_DAYS = 10


def get_open_picks() -> list[dict]:
    """
    Return all picks that are still pending outcome resolution.
    Includes picks where outcome row doesn't exist yet (new picks).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                ds.id as screen_id,
                ds.screen_date,
                ds.symbol,
                ds.setup_type,
                ds.entry,
                ds.stop,
                ds.target,
                COALESCE(to2.status, 'pending') as outcome_status,
                to2.triggered_at
            FROM daily_screens ds
            LEFT JOIN trade_outcomes to2 ON ds.id = to2.screen_id
            WHERE COALESCE(to2.status, 'pending') IN ('pending', 'triggered')
            ORDER BY ds.screen_date DESC
            """,
        ).fetchall()
    return [dict(r) for r in rows]


def init_outcome_row(screen_id: str) -> None:
    """Create a pending outcome row for a new pick if it doesn't exist."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO trade_outcomes (screen_id, status, updated_at)
            VALUES (?, 'pending', ?)
            """,
            (screen_id, datetime.utcnow().isoformat()),
        )


def fetch_recent_prices(symbol: str, days: int = EXPIRY_DAYS + 5) -> list[dict]:
    """
    Fetch recent OHLCV data for a symbol to check against trade levels.
    Returns list of dicts sorted ascending by date.
    """
    try:
        df = fetch_yfinance(symbol, lookback_days=days)
        return df.to_dict(orient="records")
    except Exception as e:
        logger.warning("Could not fetch prices for %s: %s", symbol, e)
        return []


def evaluate_pick(pick: dict, price_rows: list[dict]) -> dict | None:
    """
    Walk through price rows from the day after screen_date and determine outcome.

    Logic:
        - If high >= entry: pick is 'triggered' on that day
        - Once triggered: if low <= stop on any subsequent day → 'stopped_out'
        - Once triggered: if high >= target on any subsequent day → 'target_hit'
        - If neither after EXPIRY_DAYS: 'expired'

    Returns updated outcome dict, or None if nothing changed.
    """
    screen_date  = pick["screen_date"]
    entry        = float(pick["entry"])
    stop         = float(pick["stop"])
    target       = float(pick["target"])
    status       = pick["outcome_status"]
    triggered_at = pick.get("triggered_at")

    update = None

    for row in price_rows:
        row_date = row["date"]
        if row_date <= screen_date:
            continue  # skip the screen day itself

        high = float(row.get("high") or 0)
        low  = float(row.get("low") or 0)
        close = float(row.get("close") or 0)

        # Check expiry
        days_since = (
            date.fromisoformat(row_date) - date.fromisoformat(screen_date)
        ).days
        if days_since > EXPIRY_DAYS and status == "pending":
            update = {
                "status":     "expired",
                "closed_at":  row_date,
                "exit_price": close,
                "pnl_pct":    round((close - entry) / entry * 100, 2),
                "days_held":  days_since,
            }
            break

        if status == "pending":
            if high >= entry:
                # Entry triggered — mark triggered and keep checking for exit
                status = "triggered"
                triggered_at = row_date
                update = {
                    "status":       "triggered",
                    "triggered_at": row_date,
                }
                # Check same-day stop hit (gap open below stop)
                if low <= stop:
                    update = {
                        "status":     "stopped_out",
                        "triggered_at": row_date,
                        "closed_at":  row_date,
                        "exit_price": stop,
                        "pnl_pct":    round((stop - entry) / entry * 100, 2),
                        "days_held":  days_since,
                    }
                    status = "stopped_out"
                    break
                # Check same-day target hit
                if high >= target:
                    update = {
                        "status":     "target_hit",
                        "triggered_at": row_date,
                        "closed_at":  row_date,
                        "exit_price": target,
                        "pnl_pct":    round((target - entry) / entry * 100, 2),
                        "days_held":  days_since,
                    }
                    status = "target_hit"
                    break

        elif status == "triggered":
            days_held = (
                date.fromisoformat(row_date) - date.fromisoformat(triggered_at)
            ).days if triggered_at else days_since

            if low <= stop:
                update = {
                    "status":     "stopped_out",
                    "closed_at":  row_date,
                    "exit_price": stop,
                    "pnl_pct":    round((stop - entry) / entry * 100, 2),
                    "days_held":  days_held,
                }
                break
            if high >= target:
                update = {
                    "status":     "target_hit",
                    "closed_at":  row_date,
                    "exit_price": target,
                    "pnl_pct":    round((target - entry) / entry * 100, 2),
                    "days_held":  days_held,
                }
                break

    return update


def write_outcome_update(screen_id: str, update: dict) -> None:
    """Persist an outcome update to trade_outcomes."""
    update["updated_at"] = datetime.utcnow().isoformat()
    fields  = ", ".join(f"{k}=?" for k in update.keys())
    values  = list(update.values()) + [screen_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE trade_outcomes SET {fields} WHERE screen_id=?",
            values,
        )


def run_outcome_tracker() -> dict:
    """
    Main outcome tracker pass.
    Returns summary dict with counts of each status transition.
    """
    init_db()
    open_picks = get_open_picks()

    if not open_picks:
        logger.info("No open picks to track.")
        return {"checked": 0}

    logger.info("Tracking outcomes for %d open picks...", len(open_picks))

    summary = {"checked": 0, "triggered": 0, "stopped_out": 0, "target_hit": 0, "expired": 0}

    for pick in open_picks:
        screen_id = pick["screen_id"]
        symbol    = pick["symbol"]

        # Ensure outcome row exists
        init_outcome_row(screen_id)

        # Fetch recent prices
        price_rows = fetch_recent_prices(symbol)
        if not price_rows:
            continue

        update = evaluate_pick(pick, price_rows)
        if update:
            write_outcome_update(screen_id, update)
            new_status = update.get("status", "?")
            summary[new_status] = summary.get(new_status, 0) + 1
            logger.info(
                "  %s → %s (pnl=%.2f%%)",
                symbol, new_status, update.get("pnl_pct", 0),
            )

        summary["checked"] += 1

    logger.info("Outcome tracker complete: %s", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EOD Outcome Tracker")
    parser.add_argument(
        "--date",
        help="Target date (YYYY-MM-DD) — prices are fetched as of this date for backfill",
    )
    args = parser.parse_args()

    if args.date:
        logger.info("Running outcome tracker for date: %s", args.date)

    result = run_outcome_tracker()
    print(f"\nOutcome tracker complete: {result}")
