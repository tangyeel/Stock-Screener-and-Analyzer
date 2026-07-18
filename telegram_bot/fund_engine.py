"""
Mutual Fund Analysis Engine.

Provides rolling returns, risk metrics, benchmark comparison,
and a composite verdict (Good Purchase / Hold / Avoid).
"""

import logging
from datetime import datetime

import pandas as pd
import numpy as np
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.07


def _fetch_nav(scheme_code: str, days: int = 1095) -> pd.DataFrame | None:
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data:
            return None
        rows = []
        for entry in data["data"][-days:]:
            try:
                nav = float(entry["nav"])
                dt = datetime.strptime(entry["date"], "%d-%m-%Y")
                rows.append({"date": dt, "nav": nav})
            except (ValueError, KeyError):
                continue
        if not rows:
            return None
        return pd.DataFrame(rows).sort_values("date").set_index("date")
    except Exception as e:
        logger.warning("NAV fetch failed for %s: %s", scheme_code, e)
        return None


def _fetch_mf_details(scheme_code: str) -> dict:
    details = {}
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        meta = resp.json().get("meta", {})
        if meta.get("expense_ratio"):
            try:
                details["expense_ratio"] = float(meta["expense_ratio"].replace("%", ""))
            except (ValueError, AttributeError):
                pass
        if meta.get("aum"):
            try:
                val = meta["aum"].replace(",", "").replace(" Cr", "").replace(" cr", "").strip()
                details["aum_cr"] = float(val)
            except (ValueError, AttributeError):
                pass
    except Exception:
        pass
    return details


def _calc_cagr(nav: pd.Series, periods: int) -> float | None:
    if len(nav) < periods + 1:
        return None
    start = nav.iloc[-periods - 1]
    end = nav.iloc[-1]
    years = periods / 252
    if start <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _benchmark_returns(period_days: int) -> float | None:
    try:
        df = yf.download("^NSEI", period=f"{period_days + 60}d", progress=False)
        if df.empty:
            return None
        close = df["Close"] if "Close" in df.columns else df["Adj Close"]
        if len(close) < period_days:
            return None
        return float((close.iloc[-1] / close.iloc[-period_days]) - 1)
    except Exception:
        return None


# ── Composite scoring ──────────────────────────────────────────────────────────

def _score_return(returns: dict, vs_benchmark: dict | None) -> tuple[float, list[str]]:
    factors = 0
    total_weight = 0
    strengths = []
    concerns = []

    abs_1y = returns.get("1y")
    if abs_1y is not None:
        total_weight += 1
        if abs_1y >= 20:
            factors += 1
            strengths.append(f"1Y return {abs_1y:+.1f}% (strong)")
        elif abs_1y >= 10:
            factors += 0.7
            strengths.append(f"1Y return {abs_1y:+.1f}% (decent)")
        elif abs_1y >= 0:
            factors += 0.4
            concerns.append(f"1Y return {abs_1y:+.1f}% (modest)")
        else:
            concerns.append(f"1Y return {abs_1y:+.1f}% (negative)")

    cagr_3y = returns.get("3y_cagr")
    if cagr_3y is not None:
        total_weight += 1
        if cagr_3y >= 15:
            factors += 1
            strengths.append(f"3Y CAGR {cagr_3y:+.1f}% (strong)")
        elif cagr_3y >= 10:
            factors += 0.7
        elif cagr_3y >= 5:
            factors += 0.4
            concerns.append(f"3Y CAGR {cagr_3y:+.1f}% (modest)")
        else:
            concerns.append(f"3Y CAGR {cagr_3y:+.1f}% (low)")

    if vs_benchmark and "1y" in vs_benchmark:
        total_weight += 1
        bm = vs_benchmark["1y"]
        diff = (returns.get("1y") or 0) - bm
        if diff > 5:
            factors += 1
            strengths.append(f"Beat Nifty by {diff:+.1f}% (1Y)")
        elif diff > 0:
            factors += 0.7
            strengths.append(f"Outperformed Nifty (1Y)")
        elif diff > -5:
            factors += 0.4
            concerns.append(f"Underperformed Nifty by {diff:.1f}% (1Y)")
        else:
            concerns.append(f"Significantly underperformed Nifty (1Y)")

    score = factors / total_weight if total_weight else 0
    return score, strengths, concerns


def _score_risk(analysis: dict) -> tuple[float, list[str], list[str]]:
    factors = 0
    total_weight = 0
    strengths = []
    concerns = []

    sharpe = analysis.get("sharpe_ratio")
    if sharpe is not None:
        total_weight += 1
        if sharpe >= 1.5:
            factors += 1
            strengths.append(f"Sharpe {sharpe:.2f} (excellent risk-adjusted)")
        elif sharpe >= 1.0:
            factors += 0.7
            strengths.append(f"Sharpe {sharpe:.2f} (good)")
        elif sharpe >= 0.5:
            factors += 0.4
            concerns.append(f"Sharpe {sharpe:.2f} (moderate)")
        else:
            concerns.append(f"Sharpe {sharpe:.2f} (low)")

    sortino = analysis.get("sortino_ratio")
    if sortino is not None:
        total_weight += 1
        if sortino >= 2.0:
            factors += 1
            strengths.append(f"Sortino {sortino:.2f} (excellent downside)")
        elif sortino >= 1.5:
            factors += 0.7
        elif sortino >= 1.0:
            factors += 0.4
        else:
            concerns.append(f"Sortino {sortino:.2f} (weak downside)")

    vol = analysis.get("volatility")
    if vol is not None:
        total_weight += 1
        if vol <= 10:
            factors += 1
            strengths.append(f"Low volatility {vol:.1f}%")
        elif vol <= 15:
            factors += 0.7
        elif vol <= 20:
            factors += 0.4
            concerns.append(f"Volatility {vol:.1f}% (elevated)")
        else:
            concerns.append(f"High volatility {vol:.1f}%")

    score = factors / total_weight if total_weight else 0
    return score, strengths, concerns


def _score_consistency(analysis: dict) -> tuple[float, list[str], list[str]]:
    factors = 0
    total_weight = 0
    strengths = []
    concerns = []

    qp = analysis.get("quarterly_positive_pct")
    if qp is not None:
        total_weight += 1
        if qp >= 80:
            factors += 1
            strengths.append(f"Consistent: {qp:.0f}% quarters positive")
        elif qp >= 65:
            factors += 0.7
            strengths.append(f"Mostly consistent: {qp:.0f}% quarters positive")
        elif qp >= 50:
            factors += 0.4
        else:
            concerns.append(f"Inconsistent: only {qp:.0f}% quarters positive")

    mdd = analysis.get("max_drawdown")
    if mdd is not None:
        total_weight += 1
        if mdd >= -10:
            factors += 1
            strengths.append(f"Controlled drawdown: {mdd:.1f}%")
        elif mdd >= -20:
            factors += 0.7
            concerns.append(f"Drawdown {mdd:.1f}% (moderate)")
        elif mdd >= -30:
            factors += 0.4
            concerns.append(f"Drawdown {mdd:.1f}% (deep)")
        else:
            concerns.append(f"Severe drawdown {mdd:.1f}%")

    score = factors / total_weight if total_weight else 0
    return score, strengths, concerns


def _score_cost(expense: float | None) -> tuple[float, list[str], list[str]]:
    if expense is None:
        return 0.5, [], []
    if expense <= 0.5:
        return 1.0, [f"Low expense ratio {expense:.2f}%"], []
    if expense <= 1.0:
        return 0.7, [f"Expense ratio {expense:.2f}% (reasonable)"], []
    if expense <= 1.5:
        return 0.4, [], [f"Expense ratio {expense:.2f}% (slightly high)"]
    return 0.0, [], [f"Expense ratio {expense:.2f}% (high)"]


def synthesize_fund(analysis: dict) -> dict:
    returns = analysis.get("rolling_returns", {})
    vs_benchmark = analysis.get("vs_benchmark")

    r_score, r_str, r_conc = _score_return(returns, vs_benchmark)
    risk_score, risk_str, risk_conc = _score_risk(analysis)
    con_score, con_str, con_conc = _score_consistency(analysis)
    cost_score, cost_str, cost_conc = _score_cost(analysis.get("expense_ratio"))

    composite = round(
        0.40 * r_score + 0.25 * risk_score + 0.25 * con_score + 0.10 * cost_score, 2
    )

    if composite >= 0.65:
        verdict = "Good Purchase"
        verdict_emoji = "✅"
    elif composite >= 0.40:
        verdict = "Hold"
        verdict_emoji = "🟡"
    else:
        verdict = "Avoid"
        verdict_emoji = "❌"

    all_strengths = r_str + risk_str + con_str + cost_str
    all_concerns = r_conc + risk_conc + con_conc + cost_conc

    return {
        "composite_score": composite,
        "verdict": verdict,
        "verdict_emoji": verdict_emoji,
        "strengths": all_strengths[:4],
        "concerns": all_concerns[:4],
    }


# ── Main entry ────────────────────────────────────────────────────────────────

def analyze_fund(scheme_code: str) -> dict:
    df = _fetch_nav(scheme_code)
    if df is None or len(df) < 30:
        return {"error": "Insufficient NAV data for analysis"}

    nav = df["nav"]
    daily_returns = nav.pct_change().dropna()
    total_days = len(nav)

    rolling_returns = {}
    if total_days >= 252:
        rolling_returns["1y"] = round(float((nav.iloc[-1] / nav.iloc[-252]) - 1) * 100, 2)
    if total_days >= 756:
        cagr_3y = _calc_cagr(nav, 756)
        if cagr_3y is not None:
            rolling_returns["3y_cagr"] = round(cagr_3y * 100, 2)
    if total_days >= 1260:
        cagr_5y = _calc_cagr(nav, 1260)
        if cagr_5y is not None:
            rolling_returns["5y_cagr"] = round(cagr_5y * 100, 2)

    ann_vol = float(daily_returns.std() * np.sqrt(252))
    avg_return = float(daily_returns.mean() * 252)
    sharpe = (avg_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else 0

    downside = daily_returns[daily_returns < 0]
    downside_std = float(downside.std() * np.sqrt(252)) if len(downside) > 1 else ann_vol
    sortino = (avg_return - RISK_FREE_RATE) / downside_std if downside_std > 0 else 0

    cumulative = (1 + daily_returns).cumprod()
    running_max = cumulative.cummax()
    max_dd = float(((cumulative - running_max) / running_max).min())

    result = {
        "rolling_returns": rolling_returns,
        "volatility": round(ann_vol * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "data_points": len(nav),
    }

    if "1y" in rolling_returns:
        bm_1y = _benchmark_returns(252)
        if bm_1y is not None:
            result["vs_benchmark"] = {"1y": round(bm_1y * 100, 2)}

    if total_days >= 252:
        rolling_q = daily_returns.rolling(63).sum().dropna()
        positive_q = (rolling_q > 0).sum()
        result["quarterly_positive_pct"] = round(positive_q / len(rolling_q) * 100, 1) if len(rolling_q) else None

    details = _fetch_mf_details(scheme_code)
    if details:
        result.update(details)

    result.update(synthesize_fund(result))
    return result
