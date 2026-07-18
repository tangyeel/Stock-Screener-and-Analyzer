"""
Format analysis results into Telegram message text.
"""

import logging

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    special = set("_*[]()~`>#+-=|{}.!")
    result = ""
    for ch in str(text):
        if ch in special:
            result += "\\" + ch
        else:
            result += ch
    return result


def _fmt_pct(val) -> str:
    try:
        return f"{float(val):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def format_analysis(instrument: dict, analysis: dict, news: list[dict]) -> str:
    if "error" in analysis:
        return f"⚠️ *{_esc(instrument.get('primary_name', '?'))}*\n\n{_esc(analysis['error'])}"

    if instrument["instrument_type"] == "mutual_fund":
        return _format_fund(instrument, analysis, news)

    name = _esc(instrument.get("primary_name", "?"))
    ticker = _esc(instrument.get("ticker", "?"))
    verdict = analysis.get("verdict", "?")
    score = analysis.get("composite_score", 0)

    verdict_emoji = {"Bullish": "🟢", "Neutral": "🟡", "Bearish": "🔴"}.get(verdict, "⚪")
    categories = analysis.get("category_breakdown", {})

    msg = (
        f"📈 *{name}* \\({ticker}\\)\n"
        f"Overall: {verdict_emoji} *{verdict}* \\(score: {_esc(str(score))}/1\\.0\\)\n\n"
    )

    cat_labels = {
        "trend": "Trend",
        "momentum": "Momentum",
        "volume": "Volume/Accum",
        "volatility_structure": "Volatility",
        "relative_strength": "Rel\\. Strength",
    }
    for key, label in cat_labels.items():
        cat = categories.get(key, {})
        v = cat.get("verdict", "—")
        ve = {"Bullish": "🟢", "Neutral": "🟡", "Bearish": "🔴"}.get(v, "⚪")
        raw_s = cat.get("score")
        score_str = _esc(f"({raw_s:.2f})") if isinstance(raw_s, (int, float)) else ""
        msg += f"  {ve} *{label}*: {v} {score_str}\n"

    # Add RS details if present
    rs = analysis.get("category_results", {}).get("relative_strength", {})
    if rs.get("rs_rating"):
        msg += f"\n*RS Rating*: {_esc(str(rs['rs_rating']))}/99  \\|  *Sector RS*: {_esc(str(rs.get('sector_rs_rating', '—')))}/99\n"

    # ── Earnings ───────────────────────────────────────────────────────────────
    earnings = analysis.get("earnings")
    if earnings and earnings.get("advice"):
        msg += f"\n{_esc(earnings['advice'])}\n"

    # ── Pattern ────────────────────────────────────────────────────────────────
    pattern = analysis.get("pattern")
    if pattern:
        sig = pattern.get("signal", 0)
        emoji = "🟢" if sig > 0 else "🔴" if sig < 0 else "⚪"
        msg += f"\n{emoji} *Pattern*: {_esc(pattern.get('description', ''))}\n"

    # ── Levels ─────────────────────────────────────────────────────────────────
    levels = analysis.get("levels")
    if levels:
        sup = levels.get("nearest_support")
        res = levels.get("nearest_resistance")
        vwap = levels.get("vwap")
        pivot = levels.get("pivot")
        s1 = levels.get("s1")
        r1 = levels.get("r1")
        msg += f"\n*Levels*\n"
        if sup:
            msg += f"  Support: ₹{_esc(str(sup))}\n"
        if res:
            msg += f"  Resistance: ₹{_esc(str(res))}\n"
        if vwap:
            vs = levels.get("price_vs_vwap", "")
            msg += f"  VWAP: ₹{_esc(str(vwap))} \\({_esc(vs)}\\)\n"
        if pivot:
            msg += f"  Pivot: ₹{_esc(str(pivot))}  \\[S1: ₹{_esc(str(s1))}  \\|  R1: ₹{_esc(str(r1))}\\]\n"

    # ── Fundamentals ────────────────────────────────────────────────────────────
    fund = analysis.get("fundamentals")
    has_any = any(fund.get(k) is not None for k in ("pe", "pb", "roe", "de", "sales_growth", "div_yield"))
    if fund and has_any:
        verdict = fund.get("fundamental_verdict")
        verdict_emoji = "🟢" if verdict in ("Strong", "Fair") else "🟡" if verdict == "Mixed" else "🔴"
        line = f"\n*Fundamentals*"
        if verdict:
            line += f"  {verdict_emoji} *{verdict}*\n"
        else:
            line += "\n"
        if fund.get("pe"):
            line += f"  P/E: {_esc(str(fund['pe']))} \\({_esc(fund.get('pe_note', ''))}\\)\n"
        if fund.get("pb"):
            line += f"  P/B: {_esc(str(fund['pb']))} \\({_esc(fund.get('pb_note', ''))}\\)\n"
        if fund.get("roe"):
            line += f"  ROE: {_esc(str(fund['roe']))}% \\({_esc(fund.get('roe_note', ''))}\\)\n"
        if fund.get("de"):
            line += f"  D/E: {_esc(str(fund['de']))} \\({_esc(fund.get('de_note', ''))}\\)\n"
        if fund.get("sales_growth"):
            line += f"  Sales growth: {_esc(str(fund['sales_growth']))}%\n"
        if fund.get("div_yield"):
            line += f"  Div\\. yield: {_esc(str(fund['div_yield']))}%\n"
        if fund.get("pe_vs_sector"):
            dir = "above" if fund["pe_vs_sector"] == "above" else "below"
            sector_pe = _esc(str(fund.get("sector_pe", "")))
            line += f"  P/E vs sector: {_esc(dir)} sector median \\({sector_pe}\\)\n"
        msg += line

    # ── Trade Setup ─────────────────────────────────────────────────────────────
    setup = analysis.get("trade_setup")
    if setup:
        msg += (
            f"\n*Trade Setup*\n"
            f"  Entry zone: {_esc(setup.get('entry_zone', ''))}\n"
            f"  Stop: {_esc(setup.get('stop_loss', ''))}\n"
            f"  Target: {_esc(setup.get('target', ''))}\n"
            f"  R\\:R {_esc(setup.get('risk_reward', ''))}  \\|  Risk: {_esc(str(setup.get('risk_pct', '')))}%\n"
        )

    # News
    if news:
        msg += "\n*Recent News:*\n"
        for n in news[:4]:
            headline = _esc(n.get("headline", ""))
            source = _esc(n.get("source", ""))
            date = _esc(n.get("published_date", ""))
            msg += f"• {headline} _\\({source}, {date}\\)_ \n"

    # Source note
    src = analysis.get("source", "")
    if src == "screener_db":
        msg += f"\n_Analysis based on screener data from {_esc(str(analysis.get('run_date', '?')))}\\._\n"

    msg += "\n⚠️ Technical analysis only \\- not a recommendation to buy or sell\\."
    return msg


def _format_fund(instrument: dict, analysis: dict, news: list[dict]) -> str:
    if "error" in analysis:
        return f"⚠️ *{_esc(instrument.get('primary_name', '?'))}*\n\n{_esc(analysis['error'])}"

    name = _esc(instrument.get("primary_name", "?"))
    category = instrument.get("sector") or ""
    cat_str = f" \\({_esc(category)}\\)" if category else ""
    msg = f"📊 *{name}*{cat_str}\n"

    # ── Composite verdict ──────────────────────────────────────────────────
    verdict = analysis.get("verdict", "—")
    score = analysis.get("composite_score", 0)
    emoji = analysis.get("verdict_emoji", "⚪")
    msg += f"{emoji} *{_esc(verdict)}* \\(score: {_esc(str(score))}/1\\.0\\)\n"

    # ── Strengths & Concerns ──────────────────────────────────────────────
    strengths = analysis.get("strengths", [])
    concerns = analysis.get("concerns", [])
    if strengths:
        for s in strengths:
            msg += f"  🟢 {_esc(s)}\n"
    if concerns:
        for c in concerns:
            msg += f"  🔴 {_esc(c)}\n"

    # ── Returns vs Benchmark ─────────────────────────────────────────────
    msg += f"\n*Returns*\n"
    returns = analysis.get("rolling_returns", {})
    bm = analysis.get("vs_benchmark", {})
    if returns.get("1y") is not None:
        ret = _esc(f"{returns['1y']:+.1f}")
        line = f"  1Y: {ret}%"
        if bm.get("1y") is not None:
            bm_ret = _esc(f"{bm['1y']:+.1f}")
            diff = returns["1y"] - bm["1y"]
            beat = "🟢" if diff > 0 else "🔴" if diff < 0 else "⚪"
            line += f"  \\(Nifty: {bm_ret}%\\) {beat}"
        msg += line + "\n"
    if returns.get("3y_cagr") is not None:
        ret = _esc(f"{returns['3y_cagr']:+.1f}")
        msg += f"  3Y CAGR: {ret}%\n"
    if returns.get("5y_cagr") is not None:
        ret = _esc(f"{returns['5y_cagr']:+.1f}")
        msg += f"  5Y CAGR: {ret}%\n"

    # ── Risk ──────────────────────────────────────────────────────────────
    msg += f"\n*Risk Metrics*\n"
    msg += f"  Volatility: {_esc(_fmt_pct(analysis.get('volatility', 0)))}\n"
    msg += f"  Sharpe: {_esc(str(analysis.get('sharpe_ratio', '—')))}  \\|  Sortino: {_esc(str(analysis.get('sortino_ratio', '—')))}\n"
    msg += f"  Max Drawdown: {_esc(_fmt_pct(analysis.get('max_drawdown', 0)))}\n"

    # ── Fund Details ──────────────────────────────────────────────────────
    details_parts = []
    if analysis.get("expense_ratio"):
        details_parts.append(f"Expense: {_esc(str(analysis['expense_ratio']))}%")
    if analysis.get("aum_cr"):
        aum = analysis["aum_cr"]
        aum_str = f"₹{aum:,.0f} Cr" if aum >= 1 else f"₹{aum*100:,.0f} L"
        details_parts.append(f"AUM: {_esc(aum_str)}")
    if analysis.get("quarterly_positive_pct") is not None:
        details_parts.append(f"Q\\-positive: {_esc(str(analysis['quarterly_positive_pct']))}%")
    if details_parts:
        msg += "\n" + "  \\| ".join(details_parts) + "\n"

    # ── News ──────────────────────────────────────────────────────────────
    if news:
        msg += "\n*Recent News:*\n"
        for n in news[:3]:
            headline = _esc(n.get("headline", ""))
            source = _esc(n.get("source", ""))
            date = _esc(n.get("published_date", ""))
            msg += f"• {headline} _\\({source}, {date}\\)_ \n"

    msg += "\n⚠️ Past performance is not indicative of future results\\."
    return msg


def format_disambiguation(suggestions: list[dict]) -> str:
    msg = "Did you mean one of these?\n"
    for i, s in enumerate(suggestions[:5], 1):
        name = _esc(s.get("name", "?"))
        ticker = _esc(s.get("ticker", "?"))
        inst_type = _esc(s.get("type", "?"))
        msg += f"{i}\\. *{ticker}* — {name} {_esc('(')}{inst_type}{_esc(')')}\n"
    msg += "\nReply with a number or the exact ticker\\."
    return msg


def format_help() -> str:
    return (
        "📊 *Stock Analysis Bot*\n\n"
        "Send me any stock ticker, company name, index name, or mutual fund name "
        "and I'll run a full technical analysis\\.\n\n"
        "*Examples:*\n"
        "• `TCS` — NSE ticker\n"
        "• `hdfc bank` — common name\n"
        "• `nifty bank` — index\n"
        "• `parag parikh flexi cap` — mutual fund\n\n"
        "_Works for Nifty 100 stocks, major indices, and AMFI registered mutual funds\\._"
    )


def format_rate_limit(wait_sec: int = 60) -> str:
    return f"⏳ Too many queries\\. Please wait {wait_sec} seconds before trying again\\."


def format_backtest_report() -> str:
    """Query the latest backtest run and format a summary message."""
    from db.database import get_connection

    with get_connection() as conn:
        # First try to get the latest run with actual trades/signals
        run = conn.execute(
            """SELECT r.id, r.run_name, r.start_date, r.end_date, r.created_at
               FROM backtest_runs r
               JOIN backtest_metrics m ON r.id = m.backtest_run_id
               WHERE m.total_signals > 0
               ORDER BY r.created_at DESC LIMIT 1"""
        ).fetchone()
        
        if not run:
            # Fallback to absolute latest if none have signals
            run = conn.execute(
                """SELECT id, run_name, start_date, end_date, created_at
                   FROM backtest_runs ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()

    if not run:
        return "No backtest runs found\\. Run `python run_backtest\\.py --incremental` first\\."

    with get_connection() as conn:
        m = conn.execute(
            """SELECT * FROM backtest_metrics WHERE backtest_run_id = ?""",
            (run["id"],),
        ).fetchone()

    if not m:
        return f"Run *{_esc(run['run_name'])}* found but metrics aren't computed yet\\."

    def pct(v, digits=1):
        if v is None:
            return "—"
        return f"{v:+.{digits}f}%"

    lines = [
        f"📊 *Backtest Report: {_esc(run['run_name'])}*",
        f"_{_esc(run['start_date'])} → {_esc(run['end_date'])}_  \\|  Run: {_esc(run['created_at'][:10])}",
        "",
    ]

    metrics_lines = [
        ("Win Rate", pct(m["win_rate"])),
        ("Profit Factor", f"{m['profit_factor']:.2f}" if m["profit_factor"] else "—"),
        ("Sharpe", f"{m['sharpe_ratio']:.2f}" if m["sharpe_ratio"] else "—"),
        ("Max DD", pct(-abs(m["max_drawdown_pct"])) if m["max_drawdown_pct"] else "—"),
        ("CAGR", pct(m["cagr"])),
        ("Expectancy", pct(m["expectancy_pct"], 2)),
        ("Total Return", pct(m["total_return_pct"], 2)),
        ("Benchmark CAGR", pct(m["benchmark_cagr"])),
        ("Trades", str(m["total_triggered"]) if m["total_triggered"] else "—"),
        ("Signals", str(m["total_signals"]) if m["total_signals"] else "—"),
        ("Avg Win", pct(m["avg_win_pct"], 2)),
        ("Avg Loss", pct(-abs(m["avg_loss_pct"]), 2) if m["avg_loss_pct"] else "—"),
    ]
    for label, val in metrics_lines:
        lines.append(f"  • *{label}*: {_esc(val)}")

    lines.append("")
    lines.append("Run `python run_backtest\\.py --incremental` to update\\.")
    return "\n".join(lines)
