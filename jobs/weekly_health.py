"""
jobs/weekly_health.py — Weekly Health Check.

Queries daily_run_log for the past 7 days and sends a summary
to Telegram every Sunday evening.

Usage:
    python jobs/weekly_health.py
    python jobs/weekly_health.py --dry-run    # print message without sending

Cron (Sunday 6:00 PM IST = 12:30 PM UTC):
    30 12 * * 0 cd /path/to/screener && python jobs/weekly_health.py
"""

import sys
import logging
import argparse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_connection, init_db
from run_logging.run_logger import get_recent_runs
from notifications.telegram import send_telegram_message
from notifications.formatter import format_health_check

logger = logging.getLogger(__name__)


def compute_health_stats(days: int = 7) -> dict:
    """
    Query the last N days of run logs and compute health metrics.

    Returns:
        {
            runs_completed:      int  — successful runs
            runs_expected:       int  — expected trading days in window
            runs_skipped:        int  — weekend/holiday skips (normal)
            runs_failed:         int  — error failures (not normal)
            avg_trend_passed:    float
            avg_nse_pct:         float
            picks_sent:          int
            failures:            int
        }
    """
    runs = get_recent_runs(days=days)

    if not runs:
        return {
            "runs_completed": 0, "runs_expected": 5, "runs_skipped": 0,
            "runs_failed": 0, "avg_trend_passed": 0, "avg_nse_pct": 0,
            "picks_sent": 0, "failures": 0,
        }

    # Count expected trading days (Mon–Fri, non-holiday) in the window
    today = date.today()
    from config import NSE_HOLIDAYS
    expected = sum(
        1 for i in range(days)
        if (d := today - timedelta(days=i)).weekday() < 5
        and d.isoformat() not in NSE_HOLIDAYS
    )

    success_runs = [r for r in runs if r.get("status") == "success"]
    failed_runs  = [r for r in runs if r.get("status") == "failed"]

    picks_sent = sum(r.get("final_picks_count") or 0 for r in success_runs)

    trend_vals = [r.get("stocks_passed_trend") for r in success_runs if r.get("stocks_passed_trend") is not None]
    avg_trend  = round(sum(trend_vals) / len(trend_vals), 1) if trend_vals else 0

    nse_vals   = [r.get("data_source_primary_pct") for r in success_runs if r.get("data_source_primary_pct") is not None]
    avg_nse    = round(sum(nse_vals) / len(nse_vals), 1) if nse_vals else 0

    return {
        "runs_completed":   len(success_runs),
        "runs_expected":    expected,
        "runs_skipped":     len([r for r in runs if r.get("status") == "skipped"]),
        "runs_failed":      len(failed_runs),
        "avg_trend_passed": avg_trend,
        "avg_nse_pct":      avg_nse,
        "picks_sent":       picks_sent,
        "failures":         len(failed_runs),
    }


def get_outcome_summary(days: int = 7) -> dict:
    """
    Pull a brief outcome summary for the week.
    Returns counts by outcome status.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT to2.status, COUNT(*) as cnt
            FROM trade_outcomes to2
            JOIN daily_screens ds ON to2.screen_id = ds.id
            WHERE ds.screen_date >= ?
            GROUP BY to2.status
            """,
            (cutoff,),
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def format_full_health_message(stats: dict, outcomes: dict, week_label: str) -> str:
    """Combine run stats and outcome stats into one health check message."""
    base = format_health_check(stats, week_label)

    if outcomes:
        outcome_lines = "\n".join(
            f"  {status.replace('_', ' ').title()}: {count}"
            for status, count in sorted(outcomes.items())
        )
        base += f"\n\n📊 *Outcome summary \\(7 days\\):*\n{outcome_lines}"

    return base


def run_health_check(dry_run: bool = False) -> None:
    """Main entry point for the weekly health check."""
    init_db()

    today  = date.today()
    week_start = today - timedelta(days=6)
    week_label = f"{week_start.strftime('%b %-d')}–{today.strftime('%-d, %Y')}"

    stats    = compute_health_stats(days=7)
    outcomes = get_outcome_summary(days=7)

    logger.info("Health check stats: %s", stats)
    logger.info("Outcome summary: %s", outcomes)

    msg = format_full_health_message(stats, outcomes, week_label)

    if dry_run:
        print("\n[DRY RUN] Weekly health check message:\n")
        print(msg)
        return

    success = send_telegram_message(msg)
    if success:
        logger.info("Weekly health check sent to Telegram.")
    else:
        logger.error("Failed to send weekly health check to Telegram.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly Health Check")
    parser.add_argument("--dry-run", action="store_true", help="Print message without sending")
    args = parser.parse_args()

    run_health_check(dry_run=args.dry_run)
