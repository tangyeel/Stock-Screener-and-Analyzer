"""
backtest/report.py — Markdown report generator.

Generates a human-readable backtest report from the metrics and trades.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_report(metrics: dict, trades: list[dict],
                    signals: list[dict], run_name: str,
                    walk_forward_result: dict | None = None) -> str:
    """Generate a Markdown backtest report."""
    lines = []
    lines.append(f"# Backtest Report: {run_name}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # ── Overview ────────────────────────────────────────────────────────────
    lines.append("## Overview")
    lines.append("")
    if metrics.get("warning"):
        lines.append(f"> ⚠️ {metrics['warning']}")
        lines.append("")
        return "\n".join(lines)

    total = metrics.get("total_signals", 0)
    triggered = metrics.get("total_triggered", 0)
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|------:|")
    lines.append(f"| Total signals | {total} |")
    lines.append(f"| Triggered trades | {triggered} |")
    lines.append(f"| Win rate (excl. timed exits) | {_pct(metrics.get('win_rate', 0))} |")
    lines.append(f"| Win rate (incl. timed exits) | {_pct(metrics.get('win_rate_incl_timed_exits', 0))} |")
    lines.append(f"| Avg win | {_pct2(metrics.get('avg_win_pct', 0))} |")
    lines.append(f"| Avg loss | {_pct2(metrics.get('avg_loss_pct', 0))} |")
    lines.append(f"| Avg win/loss ratio | {metrics.get('avg_win_loss_ratio', 0):.2f} |")
    lines.append(f"| Profit factor | {metrics.get('profit_factor', 0):.2f} |")
    lines.append(f"| Expectancy per trade | {_pct2(metrics.get('expectancy_pct', 0))} |")
    lines.append("")

    # ── Risk-adjusted metrics ───────────────────────────────────────────────
    lines.append("## Risk-Adjusted Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|------:|")
    lines.append(f"| Sharpe ratio | {metrics.get('sharpe_ratio', 0):.2f} |")
    lines.append(f"| Sortino ratio | {metrics.get('sortino_ratio', 0):.2f} |")
    lines.append(f"| Calmar ratio | {metrics.get('calmar_ratio', 0):.2f} |")
    lines.append(f"| Max drawdown | {_pct2(metrics.get('max_drawdown_pct', 0))} |")
    lines.append(f"| Drawdown duration | {metrics.get('max_drawdown_duration_days', 0)} days |")
    lines.append(f"| Avg days held | {metrics.get('avg_days_held', 0):.1f} |")
    lines.append("")

    # ── Return vs Benchmark ─────────────────────────────────────────────────
    lines.append("## Returns vs Benchmark")
    lines.append("")
    lines.append(f"| Metric | Strategy | Nifty 100 Buy & Hold |")
    lines.append(f"|--------|--------:|--------------------:|")
    lines.append(f"| Total return | {_pct2(metrics.get('total_return_pct', 0))} | {_pct2(metrics.get('benchmark_return_pct', 0))} |")
    lines.append(f"| CAGR | {_pct2(metrics.get('cagr', 0))} | {_pct2(metrics.get('benchmark_cagr', 0))} |")
    lines.append(f"| Alpha | | {_pct2(metrics.get('alpha', 0))} |")
    lines.append("")

    # ── Breakdown by regime tier ────────────────────────────────────────────
    by_regime = metrics.get("metrics_by_regime_tier", {})
    if by_regime:
        lines.append("## Performance by Regime Tier")
        lines.append("")
        lines.append("| Regime | Trades | Win Rate | Avg Win | Avg Loss | Expectancy |")
        lines.append("|--------|------:|--------:|-------:|--------:|----------:|")
        for tier, data in sorted(by_regime.items()):
            lines.append(
                f"| {tier} | {data['count']} | {_pct(data['win_rate'])} "
                f"| {_pct2(data['avg_win_pct'])} | {_pct2(data['avg_loss_pct'])} "
                f"| {_pct2(data['expectancy_pct'])} |"
            )
        lines.append("")

    # ── Breakdown by setup type ─────────────────────────────────────────────
    by_setup = metrics.get("metrics_by_setup_type", {})
    if by_setup:
        lines.append("## Performance by Setup Type")
        lines.append("")
        lines.append("| Setup | Trades | Win Rate | Avg Win | Avg Loss | Expectancy |")
        lines.append("|------|------:|--------:|-------:|--------:|----------:|")
        for st, data in sorted(by_setup.items()):
            lines.append(
                f"| {st} | {data['count']} | {_pct(data['win_rate'])} "
                f"| {_pct2(data['avg_win_pct'])} | {_pct2(data['avg_loss_pct'])} "
                f"| {_pct2(data['expectancy_pct'])} |"
            )
        lines.append("")

    # ── Walk-forward ────────────────────────────────────────────────────────
    if walk_forward_result:
        lines.append("## Walk-Forward Validation")
        lines.append("")
        lines.append(f"**Windows**: {walk_forward_result.get('num_windows', 0)}"
                     f" / {walk_forward_result.get('total_windows', 0)} completed")
        lines.append("")
        lines.append(f"**Average win rate across windows**: "
                     f"{_pct(walk_forward_result.get('avg_win_rate', 0))}")
        lines.append(f"**Average expectancy across windows**: "
                     f"{_pct2(walk_forward_result.get('avg_expectancy_pct', 0))}")
        lines.append(f"**Average return across windows**: "
                     f"{_pct2(walk_forward_result.get('avg_return_pct', 0))}")
        lines.append(f"**Consistency**: {walk_forward_result.get('window_consistency_flag', 'N/A')}")
        lines.append("")
        lines.append("### Per-Window Breakdown")
        lines.append("")
        lines.append("| Window | Test Period | Signals | Triggered | Win Rate | Profit Factor | Expectancy | Return |")
        lines.append("|------:|------------|-------:|---------:|--------:|-------------:|----------:|------:|")
        for w in walk_forward_result.get("windows", []):
            lines.append(
                f"| {w['window_index'] + 1} | {w['test_start']} – {w['test_end']} "
                f"| {w['total_signals']} | {w['total_triggered']} "
                f"| {_pct(w.get('win_rate', 0))} "
                f"| {w.get('profit_factor', 0):.2f} "
                f"| {_pct2(w.get('expectancy_pct', 0))} "
                f"| {_pct2(w.get('total_return_pct', 0))} |"
            )
        lines.append("")

    # ── Best and worst trades ───────────────────────────────────────────────
    triggered_trades = [t for t in trades if t.get("triggered") and t.get("pnl_pct") is not None]
    sorted_trades = sorted(triggered_trades, key=lambda t: t.get("pnl_pct", 0))

    lines.append("## Best Trades (Top 10)")
    lines.append("")
    lines.append("| # | Symbol | Date | Exit Reason | P&L % | Days Held |")
    lines.append("|---|------:|----:|-----------:|-----:|---------:|")
    for i, t in enumerate(sorted_trades[-10:][::-1], 1):
        lines.append(
            f"| {i} | {t.get('symbol', '?')} | {t.get('signal_date', '?')} "
            f"| {t.get('exit_reason', '?')} | {_pct2(t.get('pnl_pct', 0))} "
            f"| {t.get('days_held', '—')} |"
        )
    lines.append("")

    lines.append("## Worst Trades (Bottom 10)")
    lines.append("")
    lines.append("| # | Symbol | Date | Exit Reason | P&L % | Days Held |")
    lines.append("|---|------:|----:|-----------:|-----:|---------:|")
    for i, t in enumerate(sorted_trades[:10], 1):
        lines.append(
            f"| {i} | {t.get('symbol', '?')} | {t.get('signal_date', '?')} "
            f"| {t.get('exit_reason', '?')} | {_pct2(t.get('pnl_pct', 0))} "
            f"| {t.get('days_held', '—')} |"
        )
    lines.append("")

    # ── Sample size flag ────────────────────────────────────────────────────
    if triggered < 30:
        lines.append("> ⚠️ **Small sample size**: Fewer than 30 triggered trades. "
                     "Metrics may not be statistically meaningful.")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by backtest engine*")
    return "\n".join(lines)


# ── Format helpers ───────────────────────────────────────────────────────────

def _pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val * 100:.1f}%"


def _pct2(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:+.2f}%"
