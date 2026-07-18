"""
backtest/cli.py — CLI entrypoint for the backtesting engine.

Usage:
    python run_backtest.py --start 2020-01-01 --end 2025-12-31 --run-name "v2_tiered_regime"
    python run_backtest.py --walk-forward --start 2020-01-01 --end 2025-12-31
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta

from db.database import init_db
from backtest.engine import BacktestEngine
from backtest.report import generate_report
from backtest.walk_forward import run_walk_forward

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Nifty 100 Swing Trade Backtester")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", default="backtest", help="Name for this backtest run")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk per trade %% (default 1.0)")
    parser.add_argument("--capital", type=float, default=500_000, help="Starting capital (default 500000)")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward validation")
    parser.add_argument("--report", action="store_true", help="Print markdown report after completion")
    parser.add_argument("--report-file", help="Write report to file instead of stdout")
    parser.add_argument("--no-52w-cap", action="store_true",
                        help="Disable 52-week high target cap")
    parser.add_argument("--txn-cost", type=float, default=0.25,
                        help="Round-trip transaction cost %% (default 0.25)")
    parser.add_argument("--incremental", action="store_true",
                        help="Auto-detect start date from last run (re-process last 20 days + new)")
    parser.add_argument("--json", action="store_true", help="Print metrics as JSON to stdout")
    args = parser.parse_args()

    init_db()

    if args.incremental:
        start, end = _resolve_incremental_range(args.start, args.end)
        args.start = start
        args.end = end
        logger.info("Incremental mode: backtesting %s → %s", start, end)

    if not args.start or not args.end:
        parser.error("--start and --end are required (or use --incremental to auto-detect)")

    if args.walk_forward:
        _run_walk_forward(args)
    else:
        _run_single(args)


def _resolve_incremental_range(requested_start: str | None, requested_end: str | None) -> tuple[str, str]:
    """Determine the actual backtest range for incremental mode.

    If a previous backtest run exists, start 20 trading days before its
    end_date to capture overlapping positions. Otherwise use the requested
    start or fall back to the earliest available price date.
    """
    from db.database import get_connection

    end = requested_end or datetime.today().strftime("%Y-%m-%d")

    with get_connection() as conn:
        row = conn.execute(
            """SELECT end_date FROM backtest_runs ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()

    if row:
        last_end = row["end_date"]
        # Start 20 calendar days before last end (trading days handled by engine)
        start = (datetime.strptime(last_end, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        logger.info("Previous run ended %s; overlapping from %s", last_end, start)
    elif requested_start:
        start = requested_start
    else:
        # No prior run and no start given — use earliest price date
        with get_connection() as conn:
            row = conn.execute(
                """SELECT MIN(date) as d FROM daily_prices"""
            ).fetchone()
        start = row["d"] if row and row["d"] else "2024-01-01"
        logger.info("No prior run; starting from earliest data: %s", start)

    return start, end


def _run_single(args):
    engine = BacktestEngine(
        start_date=args.start,
        end_date=args.end,
        run_name=args.run_name,
        risk_per_trade=args.risk,
        capital=args.capital,
        disable_52w_cap=args.no_52w_cap,
        transaction_cost_pct=args.txn_cost,
    )
    metrics = engine.run()

    if args.json:
        print(json.dumps(metrics))
        return

    _print_summary(metrics, engine.trades, engine.signals, args.run_name)

    if args.report:
        report = generate_report(metrics, engine.trades, engine.signals, args.run_name)
        if args.report_file:
            with open(args.report_file, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info("Report written to %s", args.report_file)
        else:
            print("\n" + report)


def _run_walk_forward(args):
    def builder(start, end, run_name):
        return BacktestEngine(
            start_date=start,
            end_date=end,
            run_name=run_name,
            risk_per_trade=args.risk,
            capital=args.capital,
            disable_52w_cap=args.no_52w_cap,
            transaction_cost_pct=args.txn_cost,
        )

    result = run_walk_forward(args.start, args.end, builder)

    print("\n=== Walk-Forward Results ===\n")
    print(f"Windows: {result['num_windows']}/{result['total_windows']}")
    print(f"Avg win rate:      {_pct(result.get('avg_win_rate'))}")
    print(f"Avg expectancy:    {_pct2(result.get('avg_expectancy_pct'))}")
    print(f"Avg return:        {_pct2(result.get('avg_return_pct'))}")
    print(f"Consistency:       {result.get('window_consistency_flag', 'N/A')}")
    print()

    if args.report:
        wf_agg = result
        report = generate_report(
            {}, [], [], args.run_name,
            walk_forward_result=wf_agg,
        )
        print(report)


def _print_summary(metrics: dict, trades: list, signals: list, run_name: str):
    print(f"\n=== Backtest: {run_name} ===\n")
    if metrics.get("warning"):
        print(f"  ⚠️ {metrics['warning']}")
        return

    triggered = [t for t in trades if t.get("triggered")]
    print(f"  Signals:               {metrics.get('total_signals', 0)}")
    print(f"  Triggered trades:      {metrics.get('total_triggered', 0)}")
    print(f"  Win rate:              {_pct(metrics.get('win_rate', 0))}")
    print(f"  Win rate (incl timed): {_pct(metrics.get('win_rate_incl_timed_exits', 0))}")
    print(f"  Avg win / loss:        {_pct2(metrics.get('avg_win_pct', 0))} / {_pct2(0 - metrics.get('avg_loss_pct', 0))}")
    print(f"  Profit factor:         {metrics.get('profit_factor', 0):.2f}")
    print(f"  Expectancy/trade:      {_pct2(metrics.get('expectancy_pct', 0))}")
    print(f"  Sharpe:                {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"  Sortino:               {metrics.get('sortino_ratio', 0):.2f}")
    print(f"  Max drawdown:          {_pct2(0 - metrics.get('max_drawdown_pct', 0))}")
    print(f"  CAGR:                  {_pct2(metrics.get('cagr', 0))}")
    print(f"  Benchmark CAGR:        {_pct2(metrics.get('benchmark_cagr', 0))}")
    print(f"  Alpha:                 {_pct2(metrics.get('alpha', 0))}")
    print()


def _pct(val):
    if val is None:
        return "—"
    return f"{val:.1f}%"


def _pct2(val):
    if val is None:
        return "—"
    return f"{val:+.2f}%"


if __name__ == "__main__":
    main()
