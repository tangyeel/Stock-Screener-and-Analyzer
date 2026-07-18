"""
backtest/walk_forward.py — Walk-forward validation with self-optimisation.

Splits the backtest period into rolling train/test windows.
Trains (optimises parameters) on a 12-month window, tests on the following 3 months
using the best parameters found, then rolls forward by 3 months and repeats.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta

import numpy as np

from db.database import get_connection
from backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)

TRAIN_MONTHS = 12
TEST_MONTHS = 3
ROLL_MONTHS = 3


def optimize_parameters(train_start: str, train_end: str) -> dict:
    """Find the best parameter combination on the train window.
    
    Grid sweeps:
      - rs_threshold: [70, 80, 90]
      - trend_conditions_required: [6, 7, 8]
    
    Optimises for maximum expectancy_pct.
    """
    logger.info("⚙️ Optimising parameters on train window: %s → %s", train_start, train_end)
    
    rs_grid = [70, 80, 90]
    trend_grid = [6, 7, 8]
    
    best_expectancy = -99999.0
    best_params = {"rs_threshold": 70, "trend_conditions_required": 7}
    
    for rst in rs_grid:
        for tc in trend_grid:
            logger.debug("Testing train params: rs_threshold=%d, trend_conditions=%d", rst, tc)
            train_engine = BacktestEngine(
                start_date=train_start,
                end_date=train_end,
                run_name=f"opt_train_{rst}_{tc}",
                rs_threshold_override=rst,
                trend_conditions_required=tc
            )
            try:
                metrics = train_engine.run()
                exp = metrics.get("expectancy_pct")
                if exp is None:
                    exp = -99999.0
                
                logger.info(
                    "  [Train rs=%d, tc=%d] -> Expectancy: %+.2f%% (Return: %+.2f%%, Signals: %d)",
                    rst, tc, exp, metrics.get("total_return_pct", 0.0), metrics.get("total_signals", 0)
                )
                
                if exp > best_expectancy:
                    best_expectancy = exp
                    best_params = {"rs_threshold": rst, "trend_conditions_required": tc}
            except Exception as e:
                logger.warning("  Train run failed for rs=%d, tc=%d: %s", rst, tc, e)
                
    logger.info("🏆 Best training parameters: %s (Expectancy: %+.2f%%)", best_params, best_expectancy)
    return best_params


def run_walk_forward(start_date: str, end_date: str, engine_builder) -> dict:
    """Run walk-forward validation with parameter optimization.

    Args:
        start_date: start of the full period (ISO)
        end_date:   end of the full period (ISO)
        engine_builder: callable that returns a BacktestEngine for a given
                        (start, end, run_name) triple.

    Returns:
        dict with per-window results and aggregate metrics.
    """
    windows = _build_windows(start_date, end_date)
    all_window_results = []

    for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
        logger.info(
            "▶️ Walk-forward window %d/%d: train=%s→%s  test=%s→%s",
            i + 1, len(windows), train_start, train_end, test_start, test_end,
        )

        # 1. Optimize parameters on the training window
        best_params = optimize_parameters(train_start, train_end)

        # 2. Run the test window using the optimized parameters
        engine = BacktestEngine(
            start_date=test_start,
            end_date=test_end,
            run_name=f"wf_window_{i + 1}",
            rs_threshold_override=best_params["rs_threshold"],
            trend_conditions_required=best_params["trend_conditions_required"]
        )
        
        try:
            metrics = engine.run()
        except Exception as e:
            logger.warning("Walk-forward window %d failed: %s", i + 1, e)
            continue

        window_result = {
            "window_index": i,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "total_signals": metrics.get("total_signals", 0),
            "total_triggered": metrics.get("total_triggered", 0),
            "win_rate": metrics.get("win_rate"),
            "profit_factor": metrics.get("profit_factor"),
            "expectancy_pct": metrics.get("expectancy_pct"),
            "total_return_pct": metrics.get("total_return_pct"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "rs_threshold": best_params["rs_threshold"],
            "trend_conditions_required": best_params["trend_conditions_required"],
        }
        all_window_results.append(window_result)
        _save_window(engine.run_id, window_result)

    # Aggregate
    triggered_all = [r.get("total_triggered", 0) for r in all_window_results]
    win_rates = [r.get("win_rate") for r in all_window_results if r.get("win_rate") is not None]
    expectancies = [r.get("expectancy_pct") for r in all_window_results if r.get("expectancy_pct") is not None]
    returns = [r.get("total_return_pct") for r in all_window_results if r.get("total_return_pct") is not None]

    return {
        "num_windows": len(all_window_results),
        "total_windows": len(windows),
        "windows": all_window_results,
        "avg_triggered_per_window": round(float(np.mean(triggered_all)), 1) if triggered_all else 0,
        "avg_win_rate": round(float(np.mean(win_rates)), 4) if win_rates else None,
        "avg_expectancy_pct": round(float(np.mean(expectancies)), 2) if expectancies else None,
        "avg_return_pct": round(float(np.mean(returns)), 2) if returns else None,
        "win_rate_std": round(float(np.std(win_rates, ddof=1)), 4) if len(win_rates) > 1 else 0,
        "window_consistency_flag": (
            "[WARN] Performance varies significantly across windows"
            if np.std(win_rates, ddof=1) > 0.20 and len(win_rates) > 1
            else "[OK] Consistent across windows"
        ) if win_rates else None,
    }


def _build_windows(start_date: str, end_date: str) -> list[tuple[str, str, str, str]]:
    """Build rolling train/test window date ranges."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    windows = []
    train_start = start
    while True:
        train_end = _add_months(train_start, TRAIN_MONTHS)
        test_start = _add_months(train_end, 1)  # one day gap to avoid lookahead
        test_end = _add_months(test_start, TEST_MONTHS)

        if test_end > end:
            break

        windows.append((
            train_start.strftime("%Y-%m-%d"),
            train_end.strftime("%Y-%m-%d"),
            test_start.strftime("%Y-%m-%d"),
            test_end.strftime("%Y-%m-%d"),
        ))

        train_start = _add_months(train_start, ROLL_MONTHS)

    return windows


def _add_months(dt: datetime, months: int) -> datetime:
    """Add N months to a date, handling year rollover."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return datetime(year, month, day)


def _save_window(run_id: str, result: dict) -> None:
    """Save a single walk-forward window result."""
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO backtest_walkforward_windows
               (id, backtest_run_id, window_index, train_start, train_end,
                test_start, test_end, total_signals, total_triggered,
                win_rate, profit_factor, expectancy_pct, total_return_pct,
                max_drawdown_pct, rs_threshold, trend_conditions_required, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (str(uuid.uuid4()), run_id, result["window_index"],
             result["train_start"], result["train_end"],
             result["test_start"], result["test_end"],
             result["total_signals"], result["total_triggered"],
             result.get("win_rate"), result.get("profit_factor"),
             result.get("expectancy_pct"), result.get("total_return_pct"),
             result.get("max_drawdown_pct"), result.get("rs_threshold"),
             result.get("trend_conditions_required")),
        )
