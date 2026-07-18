"""
logging/run_logger.py — Master run log management.

Every execution attempt — skip, success, or failure — creates exactly
one row in daily_run_log. This is how you answer "did the system run
today, and what happened" without digging through files.
"""

import uuid
import logging
from datetime import date, datetime

from db.database import get_connection  # noqa: E402

logger = logging.getLogger(__name__)


def start_run() -> str:
    """
    Create a new run record with status='running'.
    Returns the run_id (UUID string).
    """
    run_id   = str(uuid.uuid4())
    run_date = date.today().isoformat()
    started  = datetime.utcnow().isoformat()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO daily_run_log (run_id, run_date, started_at, status)
            VALUES (?, ?, ?, 'running')
            """,
            (run_id, run_date, started),
        )

    logger.info("Run started — run_id=%s date=%s", run_id, run_date)
    return run_id


def mark_skipped(run_id: str, reason: str) -> None:
    """
    Mark a run as skipped (weekend or holiday).
    reason: 'weekend' | 'holiday'
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE daily_run_log
            SET status='skipped', skip_reason=?, finished_at=?
            WHERE run_id=?
            """,
            (reason, datetime.utcnow().isoformat(), run_id),
        )
    logger.info("Run skipped — reason=%s run_id=%s", reason, run_id)


def mark_complete(run_id: str, stats: dict) -> None:
    """
    Mark a run as successfully completed and write pipeline stats.

    stats dict keys (all optional, default 0/None):
        stocks_ingested, stocks_passed_liquidity, stocks_passed_trend,
        stocks_passed_setup, final_picks_count, market_regime,
        data_source_primary_pct
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE daily_run_log SET
                status                  = 'success',
                finished_at             = ?,
                stocks_ingested         = ?,
                stocks_passed_liquidity = ?,
                stocks_passed_trend     = ?,
                stocks_passed_setup     = ?,
                final_picks_count       = ?,
                market_regime           = ?,
                data_source_primary_pct = ?
            WHERE run_id = ?
            """,
            (
                datetime.utcnow().isoformat(),
                stats.get("stocks_ingested", 0),
                stats.get("stocks_passed_liquidity", 0),
                stats.get("stocks_passed_trend", 0),
                stats.get("stocks_passed_setup", 0),
                stats.get("final_picks_count", 0),
                stats.get("market_regime"),
                stats.get("data_source_primary_pct"),
                run_id,
            ),
        )
    logger.info("Run complete — picks=%d run_id=%s", stats.get("final_picks_count", 0), run_id)


def mark_failed(run_id: str, error: str) -> None:
    """Mark a run as failed with the error message."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE daily_run_log SET
                status        = 'failed',
                finished_at   = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            (datetime.utcnow().isoformat(), str(error)[:2000], run_id),
        )
    logger.error("Run failed — error=%s run_id=%s", str(error)[:200], run_id)


def get_recent_runs(days: int = 7) -> list[dict]:
    """
    Return the last N days of run log rows, newest first.
    Used by the weekly health check job.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM daily_run_log
            WHERE run_date >= date('now', ?)
            ORDER BY started_at DESC
            """,
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]
