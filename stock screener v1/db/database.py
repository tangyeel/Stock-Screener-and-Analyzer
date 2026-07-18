"""
db/database.py — SQLite connection and schema management.

All tables are created here on first run via init_db().
Never delete rows from daily_prices or daily_indicators —
they are the historical record for backtesting.
"""

import sqlite3
import logging
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    """Context manager that yields a SQLite connection and commits on exit."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    Create all tables if they don't already exist.
    Safe to call on every startup — uses IF NOT EXISTS throughout.
    """
    with get_connection() as conn:
        _create_tables(conn)
    logger.info("Database initialised at %s", DB_PATH)


def _create_tables(conn: sqlite3.Connection) -> None:
    statements = [
        # ── Universe ────────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS nifty100_constituents (
            symbol        TEXT PRIMARY KEY,
            company_name  TEXT,
            sector        TEXT,
            added_date    TEXT,
            removed_date  TEXT,
            is_active     INTEGER DEFAULT 1
        )
        """,

        # ── Raw prices ─────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol        TEXT    NOT NULL,
            date          TEXT    NOT NULL,
            open          REAL,
            high          REAL,
            low           REAL,
            close         REAL,
            volume        INTEGER,
            delivery_pct  REAL,
            source        TEXT,
            PRIMARY KEY (symbol, date)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date)",

        # ── Computed indicators ────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS daily_indicators (
            symbol                  TEXT    NOT NULL,
            date                    TEXT    NOT NULL,
            sma20                   REAL,
            sma50                   REAL,
            sma150                  REAL,
            sma200                  REAL,
            sma200_slope            REAL,
            atr14                   REAL,
            rsi14                   REAL,
            avg_vol_20              REAL,
            week52_high             REAL,
            week52_low              REAL,
            consolidation_tightness REAL,
            delivery_pct_slope      REAL,
            rs_rating               INTEGER,
            sector_rs_rating        INTEGER,
            PRIMARY KEY (symbol, date)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_indicators_date ON daily_indicators(date)",

        # ── Sector index prices ────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS sector_index_prices (
            sector      TEXT NOT NULL,
            date        TEXT NOT NULL,
            close       REAL,
            sma50       REAL,
            sma200      REAL,
            PRIMARY KEY (sector, date)
        )
        """,

        # ── Market breadth daily ───────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS market_breadth_daily (
            date              TEXT PRIMARY KEY,
            pct_above_sma50   REAL,
            pct_above_sma200  REAL,
            india_vix         REAL,
            nifty100_close    REAL,
            nifty100_sma50    REAL,
            nifty100_sma200   REAL
        )
        """,

        # ── Run log ────────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS daily_run_log (
            run_id                   TEXT PRIMARY KEY,
            run_date                 TEXT,
            started_at               TEXT,
            finished_at              TEXT,
            status                   TEXT,
            skip_reason              TEXT,
            error_message            TEXT,
            stocks_ingested          INTEGER,
            stocks_passed_liquidity  INTEGER,
            stocks_passed_trend      INTEGER,
            stocks_passed_setup      INTEGER,
            final_picks_count        INTEGER,
            market_regime            TEXT,
            data_source_primary_pct  REAL
        )
        """,

        # ── Ingestion log ──────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS ingestion_log (
            id           TEXT PRIMARY KEY,
            run_id       TEXT,
            symbol       TEXT,
            status       TEXT,
            source       TEXT,
            rows_fetched INTEGER,
            error        TEXT,
            created_at   TEXT
        )
        """,

        # ── Regime log ─────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS regime_log (
            run_id              TEXT PRIMARY KEY,
            run_date            TEXT,
            index_close         REAL,
            index_sma50         REAL,
            index_sma200        REAL,
            pct_above_sma50     REAL,
            pct_above_sma200    REAL,
            india_vix           REAL,
            tier                TEXT,
            max_picks           INTEGER,
            rs_threshold        INTEGER,
            risk_multiplier     REAL,
            override_candidates TEXT,
            regime              TEXT,
            passed              INTEGER
        )
        """,

        # ── Filter log ─────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS filter_log (
            id         TEXT PRIMARY KEY,
            run_id     TEXT,
            symbol     TEXT,
            stage      TEXT,
            passed     INTEGER,
            details    TEXT,
            created_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_filter_run ON filter_log(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_filter_stage ON filter_log(stage)",
        "CREATE INDEX IF NOT EXISTS idx_filter_symbol ON filter_log(symbol)",

        # ── Final picks ────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS daily_screens (
            id                 TEXT PRIMARY KEY,
            run_id             TEXT,
            screen_date        TEXT,
            symbol             TEXT,
            setup_type         TEXT,
            entry              REAL,
            stop               REAL,
            target             REAL,
            risk_pct           REAL,
            reward_risk_ratio  REAL,
            rs_rating          INTEGER,
            sector_rs_rating   INTEGER,
            score              REAL,
            shares_suggested   INTEGER,
            market_regime      TEXT,
            regime_tier        TEXT,
            is_override        INTEGER DEFAULT 0,
            effective_risk_pct REAL,
            sent_to_telegram   INTEGER DEFAULT 0
        )
        """,

        # ── Telegram delivery log ──────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS telegram_log (
            id           TEXT PRIMARY KEY,
            run_id       TEXT,
            message_text TEXT,
            success      INTEGER,
            api_response TEXT,
            sent_at      TEXT
        )
        """,

        # ── Trade outcomes ─────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS trade_outcomes (
            screen_id    TEXT PRIMARY KEY REFERENCES daily_screens(id),
            status       TEXT,
            triggered_at TEXT,
            closed_at    TEXT,
            exit_price   REAL,
            pnl_pct      REAL,
            days_held    INTEGER,
            updated_at   TEXT
        )
        """,

        # ── Telegram Bot: Instrument Master ─────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS instrument_master (
            id              TEXT PRIMARY KEY,
            instrument_type TEXT,
            ticker          TEXT,
            primary_name    TEXT,
            aliases         TEXT,
            sector          TEXT,
            exchange        TEXT,
            is_active       INTEGER DEFAULT 1
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_im_ticker ON instrument_master(ticker)",
        "CREATE INDEX IF NOT EXISTS idx_im_type ON instrument_master(instrument_type)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_im_unique_ticker_type ON instrument_master(ticker, instrument_type)",

        # ── Telegram Bot: Resolution Log ────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS resolution_log (
            id              TEXT PRIMARY KEY,
            chat_id         TEXT,
            raw_query       TEXT,
            resolved_ticker TEXT,
            resolved_type   TEXT,
            confidence      TEXT,
            method          TEXT,
            score           REAL,
            created_at      TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_rl_chat ON resolution_log(chat_id)",

        # ── Telegram Bot: Analysis Log ──────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS analysis_log (
            id              TEXT PRIMARY KEY,
            query_log_id    TEXT,
            category        TEXT,
            score           REAL,
            verdict         TEXT,
            signals         TEXT,
            created_at      TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_al_query ON analysis_log(query_log_id)",

        # ── Telegram Bot: News Log ──────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS news_log (
            id              TEXT PRIMARY KEY,
            chat_id         TEXT,
            ticker          TEXT,
            headline        TEXT,
            source          TEXT,
            published_date  TEXT,
            category        TEXT,
            url             TEXT,
            created_at      TEXT
        )
        """,

        # ── Telegram Bot: Query Log ─────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS query_log (
            id                TEXT PRIMARY KEY,
            chat_id           TEXT,
            raw_query         TEXT,
            resolved_ticker   TEXT,
            instrument_type   TEXT,
            composite_score   REAL,
            verdict           TEXT,
            response_time_ms  INTEGER,
            news_items_count  INTEGER,
            status            TEXT,
            error             TEXT,
            created_at        TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_ql_chat ON query_log(chat_id)",
        # ── Backtest: Run metadata ───────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id              TEXT PRIMARY KEY,
            run_name        TEXT,
            start_date      TEXT,
            end_date        TEXT,
            walk_forward    INTEGER DEFAULT 0,
            parameters      TEXT,          -- JSON snapshot of thresholds/weights
            regime_tiers    TEXT,          -- JSON snapshot of regime tier config
            created_at      TEXT
        )
        """,

        # ── Backtest: Individual trades ───────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id                TEXT PRIMARY KEY,
            backtest_run_id   TEXT REFERENCES backtest_runs(id),
            symbol            TEXT,
            signal_date       TEXT,
            setup_type        TEXT,
            regime_tier       TEXT,
            entry_price       REAL,
            stop_price        REAL,
            target_price      REAL,
            triggered         INTEGER DEFAULT 0,
            trigger_date      TEXT,
            exit_date         TEXT,
            exit_reason       TEXT,   -- 'target_hit' | 'stopped_out' | 'timed_exit'
            exit_price        REAL,
            pnl_pct           REAL,
            days_held         INTEGER,
            rs_rating_at_entry INTEGER,
            score_at_entry    REAL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_bt_run ON backtest_trades(backtest_run_id)",

        # ── Backtest: Aggregate metrics ──────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS backtest_metrics (
            backtest_run_id            TEXT PRIMARY KEY REFERENCES backtest_runs(id),
            total_signals              INTEGER,
            total_triggered            INTEGER,
            win_rate                   REAL,
            win_rate_incl_timed_exits  REAL,
            avg_win_pct                REAL,
            avg_loss_pct               REAL,
            avg_win_loss_ratio         REAL,
            profit_factor              REAL,
            expectancy_pct             REAL,
            sharpe_ratio               REAL,
            sortino_ratio              REAL,
            max_drawdown_pct           REAL,
            max_drawdown_duration_days INTEGER,
            avg_days_held              REAL,
            total_return_pct           REAL,
            cagr                       REAL,
            benchmark_return_pct       REAL,
            benchmark_cagr             REAL,
            alpha                      REAL,
            calmar_ratio               REAL,
            metrics_by_regime_tier     TEXT,   -- JSON
            metrics_by_setup_type      TEXT,   -- JSON
            num_walkforward_windows    INTEGER,
            walkforward_avg_win_rate   REAL,
            walkforward_avg_expectancy REAL,
            computed_at                TEXT
        )
        """,

        # ── Backtest: Walk-forward windows ──────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS backtest_walkforward_windows (
            id                TEXT PRIMARY KEY,
            backtest_run_id   TEXT REFERENCES backtest_runs(id),
            window_index      INTEGER,
            train_start       TEXT,
            train_end         TEXT,
            test_start        TEXT,
            test_end          TEXT,
            total_signals     INTEGER,
            total_triggered   INTEGER,
            win_rate          REAL,
            profit_factor     REAL,
            expectancy_pct    REAL,
            total_return_pct  REAL,
            max_drawdown_pct  REAL,
            created_at        TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_wf_run ON backtest_walkforward_windows(backtest_run_id)",
    ]

    for stmt in statements:
        conn.execute(stmt)

    _apply_migrations(conn)

    logger.debug("All tables verified/created.")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns that may not exist yet in older databases."""
    migrations = [
        "ALTER TABLE daily_indicators ADD COLUMN sector_rs_rating INTEGER",
        "ALTER TABLE regime_log ADD COLUMN pct_above_sma50 REAL",
        "ALTER TABLE regime_log ADD COLUMN pct_above_sma200 REAL",
        "ALTER TABLE regime_log ADD COLUMN india_vix REAL",
        "ALTER TABLE regime_log ADD COLUMN tier TEXT",
        "ALTER TABLE regime_log ADD COLUMN max_picks INTEGER",
        "ALTER TABLE regime_log ADD COLUMN rs_threshold INTEGER",
        "ALTER TABLE regime_log ADD COLUMN risk_multiplier REAL",
        "ALTER TABLE regime_log ADD COLUMN override_candidates TEXT",
        "ALTER TABLE daily_screens ADD COLUMN sector_rs_rating INTEGER",
        "ALTER TABLE daily_screens ADD COLUMN regime_tier TEXT",
        "ALTER TABLE daily_screens ADD COLUMN is_override INTEGER DEFAULT 0",
        "ALTER TABLE daily_screens ADD COLUMN effective_risk_pct REAL",
        "ALTER TABLE backtest_walkforward_windows ADD COLUMN rs_threshold INTEGER",
        "ALTER TABLE backtest_walkforward_windows ADD COLUMN trend_conditions_required INTEGER",
    ]
    for migration in migrations:
        try:
            conn.execute(migration)
            logger.debug("Migration applied: %s", migration)
        except Exception:
            pass


if __name__ == "__main__":
    init_db()
    print(f"Database ready at: {DB_PATH}")


# Shared filter log accumulator
_filter_logs_accumulator = []

def accumulate_filter_log(run_id: str, symbol: str, stage: str, passed: bool, details: dict) -> None:
    """Accumulate a filter log in memory."""
    import json
    import uuid
    global _filter_logs_accumulator
    _filter_logs_accumulator.append((
        str(uuid.uuid4()),
        run_id,
        symbol,
        stage,
        1 if passed else 0,
        json.dumps(details)
    ))

def flush_filter_logs(dry_run: bool = False) -> None:
    """Flush all accumulated filter logs to the database in a single transaction."""
    global _filter_logs_accumulator
    if not dry_run and _filter_logs_accumulator:
        logger.info("Flushing %d filter logs to database...", len(_filter_logs_accumulator))
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO filter_log (id, run_id, symbol, stage, passed, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                _filter_logs_accumulator
            )
    _filter_logs_accumulator.clear()
