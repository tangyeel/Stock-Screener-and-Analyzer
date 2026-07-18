"""Backtesting dashboard — run selector, KPIs, equity curve, stock/duration/timeline breakdowns, walk-forward, trade log."""

import json
import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np

from dashboard.data.queries import (
    q_backtest_runs, q_backtest_trades, q_backtest_metrics,
    q_backtest_walkforward, q_regime_log,
)
from dashboard.charts.plots import (
    kpi_card, equity_curve, drawdown_chart, bar_chart, scatter,
    histogram, regime_overlay, DARK_THEME,
)
from dashboard.utils import fmt_pct, fmt_num, fmt_int, color_for_metric, safe_div


def render():
    st.title("Backtest Analysis")

    runs = q_backtest_runs()
    if runs.empty:
        st.info("No backtest runs found. Run `python run_backtest.py --start ... --end ...` first.")
        return

    # ── Run selector ──────────────────────────────────────────────────────
    run_names = _build_run_labels(runs)
    run_ids = runs["id"].tolist()

    col_sel, col_compare = st.columns([2, 1])
    with col_sel:
        selected_idx = st.selectbox(
            "Select Run",
            range(len(run_names)),
            format_func=lambda i: run_names[i],
        )
        run_id = run_ids[selected_idx]

    with col_compare:
        compare_idx = st.selectbox(
            "Compare with",
            [-1] + list(range(len(run_names))),
            format_func=lambda i: "None" if i == -1 else run_names[i],
            index=0,
        )
        compare_id = run_ids[compare_idx] if compare_idx >= 0 else None

    trades = q_backtest_trades(run_id).copy()
    metrics = q_backtest_metrics(run_id)
    wf = q_backtest_walkforward(run_id)
    regime_log = q_regime_log()

    compare_trades = q_backtest_trades(compare_id).copy() if compare_id else None
    compare_metrics = q_backtest_metrics(compare_id) if compare_id else None

    if trades.empty:
        st.warning("This backtest run has no trades.")
        return

    triggered = trades[trades["triggered"] == 1].copy()
    compare_triggered = (
        compare_trades[compare_trades["triggered"] == 1].copy()
        if compare_trades is not None else None
    )

    # ── Overview KPI cards ────────────────────────────────────────────────
    st.subheader("Overview")
    _render_kpis(metrics, compare_metrics)

    # ── Equity curve + Drawdown ──────────────────────────────────────────
    col_eq, col_dd = st.columns([3, 2])
    with col_eq:
        fig = equity_curve(triggered, title="Equity Curve")
        if compare_triggered is not None and not compare_triggered.empty:
            bench = pd.Series(index=compare_triggered["exit_date"])
            fig2 = equity_curve(compare_triggered, title="Comparison")
            for t in fig2.data:
                t.line.dash = "dot"
                t.line.color = "#6366f1"
                t.name = "Comparison"
                fig.add_trace(t)
        st.plotly_chart(fig, width='stretch')
    with col_dd:
        fig = drawdown_chart(triggered)
        if compare_triggered is not None and not compare_triggered.empty:
            fig2 = drawdown_chart(compare_triggered)
            for t in fig2.data:
                t.line.dash = "dot"
                t.line.color = "#6366f1"
                t.name = "Comparison"
                fig.add_trace(t)
        st.plotly_chart(fig, width='stretch')

    # ── Full summary table ───────────────────────────────────────────────
    if not metrics.empty:
        st.subheader("Run Summary")
        _render_summary_table(metrics, compare_metrics)

    # ── Regime-tier & Setup-type breakdown ───────────────────────────────
    if not metrics.empty:
        m = metrics.iloc[0]
        col_r, col_s = st.columns(2)
        with col_r:
            _render_breakdown_table(m.get("metrics_by_regime_tier"), "Regime Tier")
        with col_s:
            _render_breakdown_table(m.get("metrics_by_setup_type"), "Setup Type")

    if triggered.empty:
        return

    # ── P&L Distribution ─────────────────────────────────────────────────
    st.subheader("P&L Distribution")
    col_pl1, col_pl2 = st.columns(2)
    with col_pl1:
        fig = histogram(triggered, "pnl_pct", "Trade P&L Distribution", nbins=25, height=300)
        fig.add_vline(x=0, line_dash="dot", line_color="#ef4444", opacity=0.5)
        st.plotly_chart(fig, width='stretch')
    with col_pl2:
        outcome_counts = triggered["exit_reason"].value_counts().reset_index()
        outcome_counts.columns = ["Exit Reason", "Count"]
        fig = px.pie(
            outcome_counts, names="Exit Reason", values="Count",
            title="Exit Reason Breakdown", template=DARK_THEME,
            color_discrete_sequence=["#22c55e", "#ef4444", "#eab308", "#6366f1"],
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(
            height=300, showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#94a3b8", size=11),
            margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig, width='stretch')

    # ── Trigger Window Analysis ─────────────────────────────────────────
    st.subheader("Trigger Window Analysis")
    _render_trigger_analysis(trades, triggered)

    # ── Stock-wise breakdown ─────────────────────────────────────────────
    st.subheader("Stock-Wise Performance")
    _render_stock_breakdown(triggered)

    # ── Duration-wise breakdown ──────────────────────────────────────────
    st.subheader("Duration-Wise Performance")
    _render_duration_breakdown(triggered)

    # ── Timeline deep-dive ───────────────────────────────────────────────
    st.subheader("Timeline Deep-Dive")
    _render_timeline(triggered, regime_log)

    # ── Trade log ─────────────────────────────────────────────────────────
    st.subheader("Full Trade Log")
    _render_trade_log(trades)

    # ── Walk-forward ─────────────────────────────────────────────────────
    if not wf.empty:
        st.subheader("Walk-Forward Validation")
        _render_walkforward(wf)

    st.caption("Data refreshes every 5 minutes.")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _build_run_labels(runs: pd.DataFrame) -> list:
    if {"run_name", "start_date", "end_date"}.issubset(runs.columns) and len(runs) > 0:
        return (runs["run_name"] + "  ·  " + runs["start_date"] + " → " + runs["end_date"]).tolist()
    if "run_name" in runs.columns:
        return runs["run_name"].tolist()
    return [str(i) for i in range(len(runs))]


def _render_kpis(metrics: pd.DataFrame, compare: pd.DataFrame = None):
    if metrics.empty:
        return
    m = metrics.iloc[0]
    cm = compare.iloc[0] if compare is not None and not compare.empty else None

    kpi_configs = [
        ("Win Rate", fmt_pct(m.get("win_rate"), 1), color_for_metric(m.get("win_rate"))),
        ("Profit Factor", f"{m.get('profit_factor', 0):.2f}", color_for_metric(m.get("profit_factor"))),
        ("Sharpe", f"{m.get('sharpe_ratio', 0):.2f}", color_for_metric(m.get("sharpe_ratio"))),
        ("Sortino", f"{m.get('sortino_ratio', 0):.2f}", color_for_metric(m.get("sortino_ratio"))),
        ("CAGR", fmt_pct(m.get("cagr"), 1), color_for_metric(m.get("cagr"))),
    ]

    cols = st.columns(5)
    for i, (title, val, color) in enumerate(kpi_configs):
        with cols[i]:
            delta = None
            if cm is not None:
                if title == "Win Rate":
                    diff = (m.get("win_rate") or 0) - (cm.get("win_rate") or 0)
                    delta = f"vs compare: {diff:+.1f}pp"
                elif title == "Profit Factor":
                    diff = (m.get("profit_factor") or 0) - (cm.get("profit_factor") or 0)
                    delta = f"vs compare: {diff:+.2f}"
                elif title == "CAGR":
                    diff = (m.get("cagr") or 0) - (cm.get("cagr") or 0)
                    delta = f"vs compare: {diff:+.1f}pp"
            st.markdown(kpi_card(title, val, color, delta), unsafe_allow_html=True)

    cols2 = st.columns(4)
    kpi2_configs = [
        ("Max Drawdown", fmt_pct(-abs(m.get("max_drawdown_pct") or 0), 1), "#ef4444"),
        ("Expectancy", fmt_pct(m.get("expectancy_pct"), 2), color_for_metric(m.get("expectancy_pct"))),
        ("Avg W/L", f"{safe_div(m.get('avg_win_pct'), m.get('avg_loss_pct'), 0):.2f}x",
         color_for_metric(safe_div(m.get('avg_win_pct'), m.get('avg_loss_pct')))),
        ("Calmar", f"{m.get('calmar_ratio', 0):.2f}", color_for_metric(m.get("calmar_ratio"))),
    ]
    for i, (title, val, color) in enumerate(kpi2_configs):
        with cols2[i]:
            st.markdown(kpi_card(title, val, color), unsafe_allow_html=True)


def _render_summary_table(metrics: pd.DataFrame, compare: pd.DataFrame = None):
    m = metrics.iloc[0]
    cm = compare.iloc[0] if compare is not None and not compare.empty else None

    def _with_compare(val, compare_val, fmt_func=fmt_pct):
        base = fmt_func(val) if val is not None else "—"
        if compare_val is not None:
            diff = (val or 0) - (compare_val or 0)
            diff_str = f"{diff:+.2f}" if isinstance(val, (int, float)) else ""
            return f"{base} ({diff_str})"
        return base

    rows = []
    metric_defs = [
        ("Total Signals", "total_signals", lambda v: fmt_int(v)),
        ("Triggered Trades", "total_triggered", lambda v: fmt_int(v)),
        ("Win Rate", "win_rate", lambda v: fmt_pct(v, 1)),
        ("Win Rate (incl. Timed)", "win_rate_incl_timed_exits", lambda v: fmt_pct(v, 1)),
        ("Profit Factor", "profit_factor", lambda v: f"{v:.2f}"),
        ("Expectancy", "expectancy_pct", lambda v: fmt_pct(v, 2)),
        ("Avg Win", "avg_win_pct", lambda v: fmt_pct(v, 2)),
        ("Avg Loss", "avg_loss_pct", lambda v: fmt_pct(-abs(v or 0), 2)),
        ("Avg W/L Ratio", "avg_win_loss_ratio", lambda v: f"{v:.2f}"),
        ("Sharpe", "sharpe_ratio", lambda v: f"{v:.2f}"),
        ("Sortino", "sortino_ratio", lambda v: f"{v:.2f}"),
        ("Calmar", "calmar_ratio", lambda v: f"{v:.2f}"),
        ("Max Drawdown", "max_drawdown_pct", lambda v: fmt_pct(-abs(v or 0), 1)),
        ("Drawdown Duration", "max_drawdown_duration_days", lambda v: fmt_int(v)),
        ("Total Return", "total_return_pct", lambda v: fmt_pct(v, 2)),
        ("CAGR", "cagr", lambda v: fmt_pct(v, 2)),
        ("Benchmark Return", "benchmark_return_pct", lambda v: fmt_pct(v, 2)),
        ("Benchmark CAGR", "benchmark_cagr", lambda v: fmt_pct(v, 2)),
        ("Alpha", "alpha", lambda v: fmt_pct(v, 2)),
        ("Avg Days Held", "avg_days_held", lambda v: f"{v:.1f}d" if v else "—"),
    ]

    for label, key, fmt_fn in metric_defs:
        val = m.get(key)
        val_str = fmt_fn(val) if val is not None else "—"
        if cm is not None:
            cval = cm.get(key)
            cval_str = fmt_fn(cval) if cval is not None else "—"
            val_str = f"{val_str}  |  {cval_str}"
        rows.append({"Metric": label, "Value": val_str})

    summary = pd.DataFrame(rows)
    if cm is not None:
        summary.columns = ["Metric", "Selected  |  Comparison"]
    st.dataframe(summary, width='stretch', hide_index=True)


def _render_breakdown_table(json_str, title):
    if not json_str:
        return
    try:
        data = json.loads(json_str) if isinstance(json_str, str) else json_str
    except Exception:
        return
    if not data:
        return
    st.markdown(f"**{title}**")
    rows = []
    for key, val in data.items():
        rows.append({
            "Group": key,
            "Trades": val.get("count", 0),
            "Win Rate": f'{val.get("win_rate", 0):.1f}%',
            "Avg Win": f'{val.get("avg_win_pct", 0):+.2f}%',
            "Avg Loss": f'{-val.get("avg_loss_pct", 0):+.2f}%',
            "Expectancy": f'{val.get("expectancy_pct", 0):+.2f}%',
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


def _sharpe_series(x: pd.Series) -> float:
    if len(x) < 3:
        return 0.0
    std = x.std()
    return float(x.mean() / std * np.sqrt(252)) if std > 1e-8 else 0.0


def _profit_factor_single(df: pd.DataFrame, symbol: str) -> float:
    sub = df[df["symbol"] == symbol]["pnl_pct"]
    wins = sub[sub > 0].sum()
    losses = abs(sub[sub < 0].sum())
    return wins / losses if losses > 1e-8 else float("inf")


# ── Trigger window analysis ───────────────────────────────────────────────────

def _render_trigger_analysis(trades: pd.DataFrame, triggered: pd.DataFrame):
    total_signals = len(trades)
    n_triggered = len(triggered)
    n_expired = len(trades[trades["exit_reason"] == "expired_not_triggered"])
    trigger_rate = safe_div(n_triggered, total_signals, default=0)

    # Compute days_to_trigger
    trigger_df = triggered[
        triggered["trigger_date"].notna() & triggered["signal_date"].notna()
    ].copy()
    if not trigger_df.empty:
        trigger_df["signal_date_dt"] = pd.to_datetime(trigger_df["signal_date"])
        trigger_df["trigger_date_dt"] = pd.to_datetime(trigger_df["trigger_date"])
        trigger_df["days_to_trigger"] = (
            trigger_df["trigger_date_dt"] - trigger_df["signal_date_dt"]
        ).dt.days
        avg_days_to_trigger = trigger_df["days_to_trigger"].mean()
    else:
        avg_days_to_trigger = None

    cols = st.columns(4)
    with cols[0]:
        st.markdown(kpi_card("Trigger Rate",
                             f"{trigger_rate*100:.1f}%",
                             "#22c55e" if trigger_rate > 0.7 else "#eab308"),
                    unsafe_allow_html=True)
    with cols[1]:
        st.markdown(kpi_card("Not Triggered", fmt_int(n_expired),
                             "#ef4444" if n_expired > 0 else "#64748b"),
                    unsafe_allow_html=True)
    with cols[2]:
        st.markdown(kpi_card("Avg Days to Trigger",
                             f"{avg_days_to_trigger:.1f}d" if avg_days_to_trigger else "—",
                             "#22c55e" if avg_days_to_trigger and avg_days_to_trigger < 3 else "#eab308"),
                    unsafe_allow_html=True)
    with cols[3]:
        st.markdown(kpi_card("Max Trigger Window", "5d",
                             "#6366f1"),
                    unsafe_allow_html=True)

    col_x, col_y = st.columns(2)
    with col_x:
        if not trigger_df.empty and "days_to_trigger" in trigger_df.columns:
            fig = histogram(
                trigger_df.dropna(subset=["days_to_trigger"]),
                "days_to_trigger", "Days to Trigger Entry",
                nbins=5, height=300,
            )
            fig.update_xaxes(title="Trading Days", dtick=1)
            st.plotly_chart(fig, width='stretch')

    with col_y:
        # Trigger delay vs exit outcome
        if not trigger_df.empty and "days_to_trigger" in trigger_df.columns:
            trigger_df["delay_bucket"] = pd.cut(
                trigger_df["days_to_trigger"],
                bins=[-1, 0, 1, 2, 3, 4, 999],
                labels=["Day 0", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5+"],
            )
            grouped = trigger_df.groupby("delay_bucket", observed=True).agg(
                total=("exit_reason", "count"),
                target_hit=("exit_reason", lambda x: (x == "target_hit").sum()),
                stopped_out=("exit_reason", lambda x: (x == "stopped_out").sum()),
                timed_exit=("exit_reason", lambda x: (x == "timed_exit").sum()),
            ).reset_index()

            melt = grouped.melt(
                id_vars="delay_bucket",
                value_vars=["target_hit", "stopped_out", "timed_exit"],
                var_name="Exit Reason", value_name="Count",
            )
            fig = px.bar(
                melt, x="delay_bucket", y="Count", color="Exit Reason",
                title="Trigger Delay vs Exit Outcome",
                template=DARK_THEME, height=300,
                color_discrete_map={
                    "target_hit": "#22c55e",
                    "stopped_out": "#ef4444",
                    "timed_exit": "#eab308",
                },
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#1a2332",
                font=dict(color="#94a3b8", size=11),
                margin=dict(l=40, r=20, t=40, b=50),
                legend=dict(font=dict(size=10), orientation="h",
                            yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            fig.update_xaxes(title="Days from Signal to Trigger")
            fig.update_yaxes(title="Trades")
            st.plotly_chart(fig, width='stretch')


# ── Stock-wise breakdown ──────────────────────────────────────────────────────

def _render_stock_breakdown(triggered: pd.DataFrame):
    if triggered.empty:
        st.info("No triggered trades to analyse.")
        return
    grouped = triggered.groupby("symbol").agg(
        trades=("pnl_pct", "count"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean()),
        avg_pnl=("pnl_pct", "mean"),
        total_pnl=("pnl_pct", "sum"),
        avg_days=("days_held", "mean"),
        sharpe=("pnl_pct", _sharpe_series),
    ).reset_index()
    grouped["profit_factor"] = grouped.apply(
        lambda r: _profit_factor_single(triggered, r["symbol"]), axis=1
    )
    grouped = grouped.sort_values("trades", ascending=False)

    col1, col2 = st.columns(2)
    with col1:
        top = grouped.nlargest(15, "win_rate")
        fig = bar_chart(top, "symbol", "win_rate", title="Top 15 by Win Rate",
                        horizontal=True, height=380)
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(fig, width='stretch')
    with col2:
        bottom = grouped.nsmallest(15, "win_rate")
        fig = bar_chart(bottom, "symbol", "win_rate", title="Bottom 15 by Win Rate",
                        horizontal=True, height=380)
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(fig, width='stretch')

    grouped["abs_avg_pnl"] = grouped["avg_pnl"].abs()
    fig = scatter(grouped, "trades", "win_rate", color="avg_pnl", size="abs_avg_pnl",
                  title="Win Rate vs Trade Count (bubble = |Avg P&L|)")
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(fig, width='stretch')

    display = grouped.copy()
    display["win_rate"] = display["win_rate"].apply(lambda x: f"{x*100:.1f}%")
    display["avg_pnl"] = display["avg_pnl"].apply(lambda x: f"{x:+.2f}%")
    display["total_pnl"] = display["total_pnl"].apply(lambda x: f"{x:+.2f}%")
    display["avg_days"] = display["avg_days"].apply(lambda x: f"{x:.1f}d")
    display["sharpe"] = display["sharpe"].apply(lambda x: f"{x:.2f}")
    display["profit_factor"] = display["profit_factor"].apply(lambda x: f"{x:.2f}")
    display.columns = ["Symbol", "Trades", "Win Rate", "Avg P&L", "Total P&L",
                       "Avg Days", "Sharpe", "Profit Factor"]
    st.dataframe(display, width='stretch', hide_index=True)


# ── Duration breakdown ────────────────────────────────────────────────────────

def _render_duration_breakdown(triggered: pd.DataFrame):
    if triggered.empty:
        return
    df = triggered.copy()
    bins = [0, 3, 7, 11, 15, 999]
    labels = ["0–3 days", "4–7 days", "8–11 days", "12–15 days", "15+ days"]
    df["duration_bucket"] = pd.cut(
        df["days_held"].fillna(15), bins=bins, labels=labels, right=True
    )
    grouped = df.groupby("duration_bucket", observed=True).agg(
        trades=("pnl_pct", "count"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean()),
        avg_pnl=("pnl_pct", "mean"),
        avg_win=("pnl_pct", lambda x: x[x > 0].mean() if (x > 0).any() else 0.0),
        avg_loss=("pnl_pct", lambda x: abs(x[x <= 0].mean()) if (x <= 0).any() else 0.0),
    ).reset_index()
    grouped["expectancy"] = (
        grouped["win_rate"] * grouped["avg_win"]
        - (1 - grouped["win_rate"]) * grouped["avg_loss"]
    )

    col1, col2 = st.columns(2)
    with col1:
        fig = bar_chart(grouped, "duration_bucket", "win_rate",
                        title="Win Rate by Duration")
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig, width='stretch')
    with col2:
        fig = bar_chart(grouped, "duration_bucket", "avg_pnl",
                        title="Avg P&L by Duration")
        st.plotly_chart(fig, width='stretch')

    display = grouped.copy()
    display["win_rate"] = display["win_rate"].apply(lambda x: f"{x*100:.1f}%")
    for c in ["avg_pnl", "avg_win", "expectancy"]:
        display[c] = display[c].apply(lambda x: f"{x:+.2f}%")
    display["avg_loss"] = display["avg_loss"].apply(lambda x: f"{-x:+.2f}%")
    display.columns = ["Duration", "Trades", "Win Rate", "Avg P&L",
                       "Avg Win", "Avg Loss", "Expectancy"]
    st.dataframe(display, width='stretch', hide_index=True)


# ── Timeline deep-dive ────────────────────────────────────────────────────────

def _render_timeline(triggered: pd.DataFrame, regime_log: pd.DataFrame):
    if triggered.empty:
        return
    df = triggered.copy()
    df["month"] = pd.to_datetime(df["exit_date"]).dt.to_period("M").astype(str)

    monthly = df.groupby("month").agg(
        trades=("pnl_pct", "count"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean()),
        avg_pnl=("pnl_pct", "mean"),
        total_pnl=("pnl_pct", "sum"),
    ).reset_index()

    col1, col2 = st.columns(2)
    with col1:
        fig = bar_chart(monthly, "month", "win_rate", title="Monthly Win Rate")
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        st.plotly_chart(fig, width='stretch')
    with col2:
        fig = bar_chart(monthly, "month", "total_pnl", title="Monthly Total P&L")
        st.plotly_chart(fig, width='stretch')

    if not regime_log.empty:
        rl = regime_log.copy()
        rl["_next_date"] = rl["run_date"].shift(-1).fillna(df["exit_date"].max())
        fig = regime_overlay(rl, df)
        st.plotly_chart(fig, width='stretch')
    else:
        fig = equity_curve(df, title="Cumulative Equity Curve")
        st.plotly_chart(fig, width='stretch')

    display = monthly.copy()
    display["win_rate"] = display["win_rate"].apply(lambda x: f"{x*100:.1f}%")
    display["avg_pnl"] = display["avg_pnl"].apply(lambda x: f"{x:+.2f}%")
    display["total_pnl"] = display["total_pnl"].apply(lambda x: f"{x:+.2f}%")
    display.columns = ["Month", "Trades", "Win Rate", "Avg P&L", "Total P&L"]
    st.dataframe(display, width='stretch', hide_index=True)


# ── Trade log ──────────────────────────────────────────────────────────────────

def _render_trade_log(trades: pd.DataFrame):
    display = trades.copy()

    col1, col2, col3 = st.columns(3)
    with col1:
        symbols = ["All"] + sorted(display["symbol"].dropna().unique().tolist())
        filter_sym = st.selectbox("Symbol", symbols, key="bt_sym_filter")
    with col2:
        reasons = ["All"] + sorted(display["exit_reason"].dropna().unique().tolist())
        filter_reason = st.selectbox("Exit Reason", reasons, key="bt_reason_filter")
    with col3:
        triggered_only = st.checkbox("Triggered only", value=True, key="bt_triggered_filter")

    if filter_sym != "All":
        display = display[display["symbol"] == filter_sym]
    if filter_reason != "All":
        display = display[display["exit_reason"] == filter_reason]
    if triggered_only:
        display = display[display["triggered"] == 1]

    cols_map = {
        "symbol": "Symbol", "signal_date": "Signal Date", "trigger_date": "Trigger",
        "exit_date": "Exit", "exit_reason": "Exit Reason",
        "entry_price": "Entry", "stop_price": "Stop", "target_price": "Target",
        "exit_price": "Exit Price", "pnl_pct": "P&L%",
        "days_held": "Days", "setup_type": "Setup",
        "regime_tier": "Regime", "rs_rating_at_entry": "RS",
        "score_at_entry": "Score", "triggered": "Triggered",
    }
    cols_avail = [k for k in cols_map if k in display.columns]
    display = display[cols_avail].rename(columns=cols_map)

    for c in ["Entry", "Stop", "Target", "Exit Price"]:
        if c in display.columns:
            display[c] = display[c].apply(lambda x: f"₹{x:,.2f}" if pd.notna(x) else "—")
    if "P&L%" in display.columns:
        display["P&L%"] = display["P&L%"].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")

    st.dataframe(display, width='stretch', hide_index=True)
    csv = display.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Export to CSV", csv, "backtest_trades.csv",
                       "text/csv", width='stretch')


# ── Walk-forward ──────────────────────────────────────────────────────────────

def _render_walkforward(wf: pd.DataFrame):
    cols = st.columns(4)
    with cols[0]:
        st.markdown(kpi_card("Windows", fmt_int(len(wf)), "#6366f1"),
                    unsafe_allow_html=True)
    with cols[1]:
        avg_wr = wf["win_rate"].mean()
        st.markdown(kpi_card("Avg Win Rate", fmt_pct(avg_wr, 1) if pd.notna(avg_wr) else "—",
                             color_for_metric(avg_wr)),
                    unsafe_allow_html=True)
    with cols[2]:
        avg_exp = wf["expectancy_pct"].mean()
        st.markdown(kpi_card("Avg Expectancy", fmt_pct(avg_exp, 2) if pd.notna(avg_exp) else "—",
                             color_for_metric(avg_exp)),
                    unsafe_allow_html=True)
    with cols[3]:
        std = wf["win_rate"].std()
        consistency = "✅ Consistent" if std < 20 else "⚠️ Varies"
        st.markdown(kpi_card("Consistency", consistency,
                             "#22c55e" if std < 20 else "#eab308"),
                    unsafe_allow_html=True)

    display_cols = [c for c in ["window_index", "test_start", "test_end", "total_signals",
                                "total_triggered", "win_rate", "profit_factor",
                                "expectancy_pct", "total_return_pct", "max_drawdown_pct"]
                    if c in wf.columns]
    if len(display_cols) >= 2:
        display = wf[display_cols].copy()
        rename_map = {
            "window_index": "Window", "test_start": "Test Start", "test_end": "Test End",
            "total_signals": "Signals", "total_triggered": "Triggered",
            "win_rate": "Win Rate", "profit_factor": "Profit Factor",
            "expectancy_pct": "Expectancy", "total_return_pct": "Return",
            "max_drawdown_pct": "Max DD",
        }
        display = display.rename(columns={k: v for k, v in rename_map.items() if k in display.columns})
        if "Win Rate" in display.columns:
            display["Win Rate"] = display["Win Rate"].apply(
                lambda x: f"{x:.1f}%" if pd.notna(x) else "—"
            )
        for c in ["Expectancy", "Return", "Max DD"]:
            if c in display.columns:
                display[c] = display[c].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")
        if "Profit Factor" in display.columns:
            display["Profit Factor"] = display["Profit Factor"].apply(
                lambda x: f"{x:.2f}" if pd.notna(x) else "—"
            )
        st.dataframe(display, width='stretch', hide_index=True)
