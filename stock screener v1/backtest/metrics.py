"""
backtest/metrics.py — Industry-standard backtest performance metrics.

All metrics computed from the trade list and (optionally) an equity curve.
Formulas are documented to ensure auditability.
"""

import math
import logging
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.07  # Indian T-bill proxy; configurable


def compute_all_metrics(trades: list[dict], signals: list[dict],
                        capital: float = 500_000) -> dict:
    """Compute the full set of backtest metrics."""
    triggered = [t for t in trades if t.get("triggered")]
    num_signals = len(signals)
    num_triggered = len(triggered)

    if num_triggered < 3:
        return {
            "total_signals": num_signals,
            "total_triggered": num_triggered,
            "warning": f"Insufficient trades ({num_triggered}) for meaningful metrics",
        }

    # ── Win / Loss classification ──────────────────────────────────────────
    timed_exits = [t for t in triggered if t.get("exit_reason") == "timed_exit"]
    definitive = [t for t in triggered if t.get("exit_reason") in ("target_hit", "stopped_out")]

    winners = [t for t in definitive if (t.get("pnl_pct") or 0) > 0]
    losers = [t for t in definitive if (t.get("pnl_pct") or 0) <= 0]

    win_rate = len(winners) / len(definitive) if definitive else 0
    # Including timed_exits: treat them as wins if positive, losses if negative
    all_timed = triggered  # all triggered trades
    win_rate_incl = sum(1 for t in all_timed if (t.get("pnl_pct") or 0) > 0) / len(all_timed) if all_timed else 0

    avg_win = np.mean([t["pnl_pct"] for t in winners]) if winners else 0
    avg_loss = np.mean([abs(t["pnl_pct"]) for t in losers]) if losers else 0
    avg_win_loss = avg_win / avg_loss if avg_loss else 0

    # ── Profit factor ──────────────────────────────────────────────────────
    gross_profit = sum(t["pnl_pct"] for t in all_timed if (t["pnl_pct"] or 0) > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in all_timed if (t["pnl_pct"] or 0) < 0))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    # ── Expectancy ─────────────────────────────────────────────────────────
    loss_rate = 1 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    # ── Build equity curve from consecutive daily returns ──────────────────
    equity_curve = _build_equity_curve(trades, capital)
    daily_returns = _daily_return_series(equity_curve)

    total_return = ((equity_curve[-1] / equity_curve[0]) - 1) * 100 if len(equity_curve) > 1 else 0
    cagr = _calc_cagr(equity_curve)

    # ── Risk metrics ───────────────────────────────────────────────────────
    sharpe = _calc_sharpe(daily_returns)
    sortino = _calc_sortino(daily_returns)
    max_dd, max_dd_dur = _calc_max_drawdown(equity_curve)
    avg_days = np.mean([t["days_held"] for t in all_timed if t.get("days_held")]) if all_timed else 0
    calmar = cagr / abs(max_dd) if max_dd else 0

    # ── Benchmark ──────────────────────────────────────────────────────────
    benchmark_return, benchmark_cagr = _benchmark_stats(trades)

    # ── Breakdowns ─────────────────────────────────────────────────────────
    by_regime = _breakdown_by(trades, "regime_tier")
    by_setup = _breakdown_by(trades, "setup_type")

    return {
        "total_signals": num_signals,
        "total_triggered": num_triggered,
        "win_rate": round(win_rate * 100, 1),
        "win_rate_incl_timed_exits": round(win_rate_incl * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_win_loss_ratio": round(avg_win_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_pct": round(expectancy, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration_days": max_dd_dur,
        "avg_days_held": round(avg_days, 1),
        "total_return_pct": round(total_return, 2),
        "cagr": round(cagr * 100, 2),
        "benchmark_return_pct": round(benchmark_return, 2),
        "benchmark_cagr": round(benchmark_cagr * 100, 2),
        "alpha": round((cagr - benchmark_cagr) * 100, 2),
        "calmar_ratio": round(calmar, 2),
        "metrics_by_regime_tier": by_regime,
        "metrics_by_setup_type": by_setup,
    }


# ── Equity curve ──────────────────────────────────────────────────────────────

def _build_equity_curve(trades: list[dict], capital: float) -> list[float]:
    """Build a cumulative equity curve.

    Tracks actual capital after each trade closes using rupee PnL.
    pnl_rupee = pnl_pct * position_entry_value / 100.
    """
    equity = [capital]
    for t in trades:
        if t.get("triggered") and t.get("pnl_rupee") is not None:
            new_equity = equity[-1] + t["pnl_rupee"]
            equity.append(new_equity)
    if len(equity) == 1:
        equity.append(capital)
    return equity


def _daily_return_series(equity_curve: list[float]) -> list[float]:
    """Compute daily returns from equity curve (%
change between consecutive points)."""
    if len(equity_curve) < 2:
        return [0.0]
    returns = []
    for i in range(1, len(equity_curve)):
        r = (equity_curve[i] / equity_curve[i - 1]) - 1
        returns.append(r)
    return returns


def _calc_cagr(equity_curve: list[float], years: float | None = None) -> float:
    """CAGR from equity curve."""
    if len(equity_curve) < 2:
        return 0.0
    if years is None:
        years = len(equity_curve) / 252  # assume 252 trading days/year
    if years <= 0:
        return 0.0
    return (equity_curve[-1] / equity_curve[0]) ** (1 / years) - 1


def _calc_sharpe(daily_returns: list[float]) -> float:
    """Annualised Sharpe ratio from daily return series."""
    if len(daily_returns) < 2:
        return 0.0
    arr = np.array(daily_returns)
    mean_ret = np.mean(arr)
    std_ret = np.std(arr, ddof=1)
    if std_ret == 0:
        return 0.0
    daily_rf = RISK_FREE_RATE / 252
    return (mean_ret - daily_rf) / std_ret * math.sqrt(252)


def _calc_sortino(daily_returns: list[float]) -> float:
    """Annualised Sortino ratio (penalises only downside deviation)."""
    if len(daily_returns) < 2:
        return 0.0
    arr = np.array(daily_returns)
    mean_ret = np.mean(arr)
    downside = arr[arr < 0]
    if len(downside) == 0:
        return 10.0  # no downside at all → excellent
    downside_std = np.std(downside, ddof=1)
    if downside_std == 0:
        return 10.0
    daily_rf = RISK_FREE_RATE / 252
    return (mean_ret - daily_rf) / downside_std * math.sqrt(252)


def _calc_max_drawdown(equity_curve: list[float]) -> tuple[float, int]:
    """Maximum drawdown (as fraction) and duration in trading periods."""
    if len(equity_curve) < 2:
        return 0.0, 0
    peak = equity_curve[0]
    max_dd = 0.0
    dd_start = 0
    max_dd_dur = 0
    current_dd_dur = 0
    for i, val in enumerate(equity_curve):
        if val > peak:
            peak = val
            dd_start = i
            current_dd_dur = 0
        else:
            dd = (peak - val) / peak
            current_dd_dur = i - dd_start
            if dd > max_dd:
                max_dd = dd
                max_dd_dur = current_dd_dur
    return max_dd, max_dd_dur


# ── Benchmark ─────────────────────────────────────────────────────────────────

def _benchmark_stats(trades: list[dict]) -> tuple[float, float]:
    """Compute Nifty 100 buy-and-hold return and CAGR over the trade period."""
    if not trades:
        return 0.0, 0.0
    dates = [t.get("signal_date") for t in trades if t.get("signal_date")]
    if not dates:
        return 0.0, 0.0
    try:
        from data.yfinance_fetcher import fetch_index_history
        from config import NIFTY100_TICKER
        df = fetch_index_history(NIFTY100_TICKER, lookback_days=500)
        if df.empty:
            return 0.0, 0.0
        start_date = min(dates)
        end_date = max(
            t.get("exit_date") or t.get("signal_date") for t in trades if t.get("signal_date")
        )
        # Use nearest <= dates (trading days may not match calendar dates)
        start_row = df[df["date"] <= start_date]
        end_row = df[df["date"] <= end_date]
        if start_row.empty or end_row.empty:
            return 0.0, 0.0
        start_row = start_row.tail(1)
        end_row = end_row.tail(1)
        start_price = float(start_row["close"].iloc[0])
        end_price = float(end_row["close"].iloc[0])
        total_ret = (end_price / start_price) - 1
        days = (datetime.strptime(end_date, "%Y-%m-%d")
                - datetime.strptime(start_date, "%Y-%m-%d")).days
        years = max(days / 365.25, 0.1)
        cagr = (end_price / start_price) ** (1 / years) - 1
        return total_ret * 100, cagr
    except Exception as e:
        logger.debug("Benchmark stats failed: %s", e)
        return 0.0, 0.0


# ── Breakdowns ────────────────────────────────────────────────────────────────

def _breakdown_by(trades: list[dict], key: str) -> dict:
    """Compute win rate and expectancy broken down by a field (regime_tier or setup_type)."""
    groups = {}
    for t in trades:
        if not t.get("triggered"):
            continue
        k = t.get(key, "unknown")
        groups.setdefault(k, []).append(t)

    result = {}
    for k, group in groups.items():
        winners = [t for t in group if (t.get("pnl_pct") or 0) > 0]
        wr = len(winners) / len(group) if group else 0
        avg_w = np.mean([t["pnl_pct"] for t in winners]) if winners else 0
        avg_l = np.mean([abs(t["pnl_pct"]) for t in group if (t["pnl_pct"] or 0) <= 0]) or 0
        exp = wr * avg_w - (1 - wr) * avg_l if avg_l else wr * avg_w
        result[k] = {
            "count": len(group),
            "win_rate": round(wr * 100, 1),
            "avg_win_pct": round(avg_w, 2),
            "avg_loss_pct": round(avg_l, 2),
            "expectancy_pct": round(exp, 2),
        }
    return result
