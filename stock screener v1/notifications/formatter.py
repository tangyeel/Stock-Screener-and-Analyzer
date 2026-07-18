"""
notifications/formatter.py — Format screener picks into Telegram messages (v2).

All messages are in Telegram Markdown format.
Critical framing rule from the spec:
    "At 8 AM the market has not opened. All output is based on the previous close.
     Every 'Entry' is a conditional trigger price — never a live fill."
This disclaimer is included in every message that contains picks.
"""

from datetime import date


def format_trade_message(picks: list[dict], run_date: str = None,
                         regime: str = "bullish",
                         regime_data: dict = None) -> str:
    """
    Format the daily Telegram message (v2 spec §9.3).

    Handles three states:
    1. Zero picks — market open but nothing qualified
    2. N picks — normal output with full trade details, regime header

    Args:
        picks:       List of pick dicts from trade_params.process_picks()
        run_date:    ISO date string for display. Defaults to today.
        regime:      Regime tier name ("strong_bull" | "neutral_selective" | etc.)
        regime_data: Full regime result dict for breadth/VIX display.

    Returns:
        Formatted string ready to POST to Telegram.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    rd = regime_data or {}

    # ── Regime header ─────────────────────────────────────────────────────────
    breadth_str = f"{rd.get('pct_above_sma200', '?')}% above 200DMA"
    vix_str = f"VIX: {rd.get('india_vix', '?')}" if rd.get('india_vix') else ""
    regime_label = _regime_display(regime)

    header = (
        f"📊 *Swing Screener — {run_date}*\n"
        f"Regime: {regime_label} \\({breadth_str}"
        + (f", {vix_str}" if vix_str else "")
        + "\\)\n\n"
    )

    # ── Zero picks ────────────────────────────────────────────────────────────
    if not picks:
        if regime in ("bearish",):
            return header + (
                "No qualifying setups today — breadth and leadership both weak\\.\n\n"
                "_Next scan: tomorrow 8 AM_"
            )
        return header + (
            "No qualifying setups today\\. "
            "All candidates failed pattern or trend template filters\\.\n\n"
            "_Next scan: tomorrow 8 AM_"
        )

    #     ── Trade picks ───────────────────────────────────────────────────────────
    pick_count = len(picks)
    plural     = "setup" if pick_count == 1 else "setups"
    msg = header + f"{pick_count} {plural}\n" + "\n"

    for p in picks:
        flag_str = "⚡ High\\-conviction override\n" if p.get("is_override") else ""

        symbol     = _esc(str(p.get("symbol", "?")))
        setup_type = p.get("setup_type", "?").capitalize()
        entry      = _fmt_price(p.get("entry"))
        stop       = _fmt_price(p.get("stop"))
        target     = _fmt_price(p.get("target"))
        risk_pct   = p.get("risk_pct", 0)
        rr         = p.get("reward_risk_ratio", 0)
        rs_rating  = p.get("rs_rating") or "—"
        sector_rs  = p.get("sector_rs_rating")
        sector_rs  = sector_rs if sector_rs is not None else "—"
        shares     = p.get("shares", 0)
        eff_risk   = p.get("effective_risk_pct", p.get("risk_pct", 1.0))

        size_line = (
            f"  💰 Size:          ⚠️ Below min risk budget \\({_fmt_pct(eff_risk)} capital risk\\)"
            if shares == 0 else
            f"  💰 Size:          {shares} shares \\({_fmt_pct(eff_risk)} capital risk\\)"
        )

        msg += (
            f"▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
            f"{flag_str}*{symbol}* — {setup_type}\n"
            f"  🎯 Entry:        ₹{entry}\n"
            f"  🛑 Stop:         ₹{stop}  \\[{_fmt_pct(risk_pct)} ▼\\]\n"
            f"  ✅ Target:       ₹{target} \\(R:R {rr}:1\\)\n"
            f"  📈 RS:           {rs_rating}/99  \\│  Sector RS: {sector_rs}/99\n"
            f"{size_line}\n\n"
        )

    msg += (
        "▁" * 26 + "\n"
        "⚠️ *Conditional entries* \\- all levels based on yesterday's close\\. "
        "Confirm price action is holding before executing\\. Not financial advice\\."
    )

    return msg


def format_regime_fail_message(run_date: str, regime_data: dict) -> str:
    """
    Message sent when market regime is bearish and no override candidates found.
    Not used by the v2 pipeline directly (no hard stop at Stage 0),
    but kept for backward compatibility.
    """
    run_date = run_date or date.today().isoformat()
    tier  = regime_data.get("tier", "bearish")
    pct   = regime_data.get("pct_above_sma200", "?")
    vix   = regime_data.get("india_vix", "?")
    close = regime_data.get("nifty100_close", "N/A")

    return (
        f"📊 *Swing Screener — {run_date}*\n\n"
        f"⛔ *Market regime: {_esc(tier.upper())}*\n\n"
        f"Nifty 100 breadth is weak \\({pct}% above 200DMA, VIX: {vix}\\)\\.\n"
        f"Index close: ₹{_fmt_price(close)}\n\n"
        f"No override\\-quality candidates found\\. Skipping today\\.\n"
        f"_Next scan: tomorrow 8 AM_"
    )


def format_error_message(run_date: str, error: str) -> str:
    """Alert message sent on pipeline failure (unhandled exception)."""
    run_date = run_date or date.today().isoformat()
    short_err = _esc(str(error)[:200])
    return (
        f"⚠️ *Screener failed — {run_date}*\n\n"
        f"An unhandled error stopped the pipeline:\n"
        f"`{short_err}`\n\n"
        f"Check screener\\.log for full traceback\\."
    )


def format_health_check(stats: dict, week_label: str = None) -> str:
    """
    Weekly health check summary message.

    Args:
        stats: dict with keys: runs_completed, runs_expected, avg_trend_passed,
               avg_nse_pct, picks_sent, failures
        week_label: e.g. "Jul 7–11, 2026"
    """
    week = week_label or "Past 7 days"
    runs_ok  = stats.get("runs_completed", "?")
    runs_exp = stats.get("runs_expected", 5)
    avg_trend = stats.get("avg_trend_passed", "?")
    avg_nse   = stats.get("avg_nse_pct", "?")
    picks     = stats.get("picks_sent", "?")
    failures  = stats.get("failures", 0)

    status = "✅" if failures == 0 else "⚠️"

    return (
        f"📈 *Weekly Health Check — {_esc(week)}*\n\n"
        f"{status} Runs completed: {runs_ok}/{runs_exp}\n"
        f"📉 Avg stocks passing trend template: {avg_trend}\n"
        f"📡 Avg NSE Bhavcopy coverage: {avg_nse}%\n"
        f"💡 Picks sent this week: {picks}\n"
        f"🔴 Failures: {failures}\n\n"
        f"_All logs in screener\\.db_"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _regime_display(tier: str) -> str:
    """Map regime tier to a display-friendly string with emoji."""
    mapping = {
        "strong_bull":       "🟢 Strong Bull",
        "neutral_selective": "🟡 Neutral Selective",
        "weak_selective":    "🟠 Weak Selective",
        "bearish":           "🔴 Bearish",
    }
    return mapping.get(tier, tier)


def _fmt_price(val) -> str:
    """Format a price value to 2 decimal places, escaping for Markdown."""
    try:
        return _esc(f"{float(val):.2f}")
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pct(val: float) -> str:
    """Format a percentage value for display, escaping for Markdown."""
    try:
        return _esc(f"{float(val):.1f}%")
    except (TypeError, ValueError):
        return "N/A"


def _esc(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2.
    Characters that must be escaped: . - ( ) ! # + = | { } > ~
    """
    special = r"\.-()+!#=|{}~"
    result = ""
    for ch in str(text):
        if ch in special:
            result += "\\" + ch
        else:
            result += ch
    return result
