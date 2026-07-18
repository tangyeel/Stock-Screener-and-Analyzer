"""Screener metrics page — pipeline health, picks, live trades, trade outcomes."""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date

from dashboard.data.queries import (
    q_run_log, q_daily_screens, q_regime_log, q_trade_outcomes,
    q_constituents, q_open_trades,
)
from dashboard.charts.plots import (
    kpi_card, bar_chart, line_chart, scatter, histogram, DARK_THEME,
)
from dashboard.utils import fmt_pct, fmt_int, safe_div, color_for_metric


STATUS_COLORS = {
    "pending": "#eab308",
    "triggered": "#6366f1",
    "stopped_out": "#ef4444",
    "target_hit": "#22c55e",
    "expired": "#64748b",
    "win": "#22c55e",
    "loss": "#ef4444",
}

STATUS_BADGES = {
    s: f'<span style="display:inline-block;padding:2px 10px;border-radius:10px;'
       f'font-size:0.72rem;font-weight:600;background:{c}22;color:{c};'
       f'border:1px solid {c}44">{s.replace("_"," ").title()}</span>'
    for s, c in STATUS_COLORS.items()
}


def render():
    st.title("Pipeline & Screener")

    runs = q_run_log()
    screens = q_daily_screens()
    regimes = q_regime_log()
    outcomes = q_trade_outcomes()
    constituents = q_constituents()
    open_trades = q_open_trades()

    if runs.empty:
        st.info("No screener run data yet. Run `python main.py` first.")
        return

    # ── Pipeline KPI row ───────────────────────────────────────────────────
    total_runs = len(runs)
    success_runs = len(runs[runs.get("status", "") == "complete"])
    total_picks = len(screens)
    total_symbols = len(constituents)
    success_rate = safe_div(success_runs, total_runs, default=0)

    cols = st.columns(4)
    with cols[0]:
        st.markdown(kpi_card("Pipeline Runs", fmt_int(total_runs),
                             "#6366f1"), unsafe_allow_html=True)
    with cols[1]:
        st.markdown(kpi_card("Success Rate", f"{success_rate*100:.0f}%",
                             "#22c55e" if success_rate > 0.8 else "#eab308"),
                    unsafe_allow_html=True)
    with cols[2]:
        st.markdown(kpi_card("Total Picks", fmt_int(total_picks),
                             "#22c55e" if total_picks > 0 else "#64748b"),
                    unsafe_allow_html=True)
    with cols[3]:
        st.markdown(kpi_card("Universe", fmt_int(total_symbols),
                             "#f59e0b"), unsafe_allow_html=True)

    # ── Live Open Trades ───────────────────────────────────────────────────
    st.markdown('<div class="section-divider">Live Open Trades</div>',
                unsafe_allow_html=True)

    if not open_trades.empty:
        n_pending = len(open_trades[open_trades["status"] == "pending"])
        n_triggered = len(open_trades[open_trades["status"] == "triggered"])
        avg_waiting = (
            open_trades["days_held"].dropna().mean()
            if "days_held" in open_trades.columns else 0
        )
        cols = st.columns(4)
        with cols[0]:
            st.markdown(kpi_card("Open Trades", fmt_int(len(open_trades)),
                                 "#6366f1"), unsafe_allow_html=True)
        with cols[1]:
            st.markdown(kpi_card("Pending", fmt_int(n_pending),
                                 "#eab308"), unsafe_allow_html=True)
        with cols[2]:
            st.markdown(kpi_card("Triggered", fmt_int(n_triggered),
                                 "#6366f1"), unsafe_allow_html=True)
        with cols[3]:
            st.markdown(kpi_card("Avg Days Waiting",
                                 f"{avg_waiting:.1f}d" if avg_waiting else "—",
                                 "#22c55e" if avg_waiting < 5 else "#eab308"),
                        unsafe_allow_html=True)

        now = datetime.now()
        rows = []
        for _, t in open_trades.iterrows():
            status = t.get("status", "pending")
            days = t.get("days_held") or 0
            expiry = 10
            pct = min(days / expiry * 100, 100)
            bar_color = "#22c55e" if pct < 50 else "#eab308" if pct < 80 else "#ef4444"
            bar = (
                f'<div style="width:100%;background:#1e293b;border-radius:4px;'
                f'height:6px;margin-top:4px">'
                f'<div style="width:{pct:.0f}%;background:{bar_color};'
                f'border-radius:4px;height:6px"></div></div>'
            )
            rows.append({
                "Symbol": t.get("symbol", "—"),
                "Setup": t.get("setup_type", "—"),
                "Status": STATUS_BADGES.get(status, status),
                "Entry": f'₹{t.get("entry", 0):,.2f}' if t.get("entry") else "—",
                "Days Waiting": f'{days:.0f}d' + bar,
                "Last Updated": t.get("updated_at", "—")[:10]
                if t.get("updated_at") else "—",
            })
        display = pd.DataFrame(rows)
        st.markdown(
            display.to_html(escape=False, index=False)
            .replace('<table', '<table style="width:100%;font-size:0.8rem"'),
            unsafe_allow_html=True,
        )
    else:
        st.caption("No open trades. Run the outcome tracker or wait for picks to be active.")

    st.caption(f"Last refreshed: {datetime.now():%H:%M:%S}")

    # ── Run history table + chart ──────────────────────────────────────────
    st.markdown('<div class="section-divider">Pipeline Run History</div>',
                unsafe_allow_html=True)
    cols_avail = [c for c in ["run_date", "status", "market_regime",
                                "stocks_ingested", "final_picks_count",
                                "started_at", "finished_at"]
                  if c in runs.columns]
    if len(cols_avail) >= 2:
        runs_display = runs[cols_avail].head(30).copy()
        renames = {
            "run_date": "Date", "status": "Status", "market_regime": "Regime",
            "stocks_ingested": "Ingested", "final_picks_count": "Picks",
            "started_at": "Started", "finished_at": "Finished",
        }
        runs_display = runs_display.rename(
            columns={k: v for k, v in renames.items() if k in runs_display.columns}
        )
        st.dataframe(runs_display, width='stretch', hide_index=True)

        daily = runs.copy()
        daily["date"] = pd.to_datetime(daily["run_date"])
        daily = daily.sort_values("date")
        y_cols = [c for c in ["stocks_ingested", "final_picks_count"] if c in daily.columns]
        if y_cols:
            fig = line_chart(daily, "run_date", y_cols, "Daily Run Metrics", height=300)
            st.plotly_chart(fig, width='stretch')

    # ── Trade Outcome Analytics ────────────────────────────────────────────
    if not outcomes.empty:
        st.markdown('<div class="section-divider">Trade Outcome Analytics</div>',
                    unsafe_allow_html=True)

        outcomes_winloss = outcomes[outcomes["status"].isin(["win", "loss"])].copy()
        if not outcomes_winloss.empty:
            n_win = len(outcomes_winloss[outcomes_winloss["status"] == "win"])
            n_loss = len(outcomes_winloss[outcomes_winloss["status"] == "loss"])
            wr = safe_div(n_win, n_win + n_loss, default=0)
        else:
            outcomes_all_statuses = outcomes.copy()
            if "status" in outcomes_all_statuses.columns:
                outcomes_all_statuses.loc[
                    outcomes_all_statuses["pnl_pct"].notna(), "status2"
                ] = outcomes_all_statuses["pnl_pct"].apply(
                    lambda x: "win" if x > 0 else "loss" if x < 0 else "flat"
                )
                n_win = len(outcomes_all_statuses[
                    outcomes_all_statuses.get("status2") == "win"
                ])
                n_loss = len(outcomes_all_statuses[
                    outcomes_all_statuses.get("status2") == "loss"
                ])
                wr = safe_div(n_win, n_win + n_loss, default=0)
            else:
                n_win = n_loss = 0
                wr = 0

        avg_days_held = outcomes["days_held"].dropna().mean()
        avg_pnl = outcomes["pnl_pct"].dropna().mean()

        cols = st.columns(4)
        with cols[0]:
            st.markdown(kpi_card("Tracked Trades", fmt_int(len(outcomes)),
                                 "#6366f1"), unsafe_allow_html=True)
        with cols[1]:
            st.markdown(kpi_card("Win Rate", f"{wr*100:.1f}%",
                                 "#22c55e" if wr > 0.5 else "#ef4444"),
                        unsafe_allow_html=True)
        with cols[2]:
            st.markdown(kpi_card("Avg Days Held",
                                 f"{avg_days_held:.1f}d" if avg_days_held else "—",
                                 "#6366f1"), unsafe_allow_html=True)
        with cols[3]:
            st.markdown(kpi_card("Avg P&L",
                                 fmt_pct(avg_pnl) if avg_pnl else "—",
                                 color_for_metric(avg_pnl)),
                        unsafe_allow_html=True)

        # Outcome mix pie
        col_a, col_b = st.columns(2)
        with col_a:
            status_col = outcomes.get("status2", outcomes.get("status"))
            if status_col is not None:
                counts = status_col.value_counts().reset_index()
                counts.columns = ["Status", "Count"]
                fig = px.pie(
                    counts, names="Status", values="Count",
                    title="Outcome Mix", template=DARK_THEME,
                    color="Status",
                    color_discrete_map={
                        "win": "#22c55e", "loss": "#ef4444",
                        "pending": "#eab308", "triggered": "#6366f1",
                        "stopped_out": "#ef4444", "target_hit": "#22c55e",
                        "expired": "#64748b",
                    },
                )
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(
                    height=300, showlegend=True,
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#94a3b8", size=11),
                    margin=dict(l=20, r=20, t=40, b=20),
                    legend=dict(font=dict(size=10)),
                )
                st.plotly_chart(fig, width='stretch')

        with col_b:
            if "pnl_pct" in outcomes.columns:
                fig = histogram(
                    outcomes.dropna(subset=["pnl_pct"]), "pnl_pct",
                    "P&L Distribution", nbins=25, height=300,
                )
                fig.add_vline(x=0, line_dash="dot", line_color="#ef4444", opacity=0.5)
                st.plotly_chart(fig, width='stretch')

        # P&L scatter by days held
        if "pnl_pct" in outcomes.columns and "days_held" in outcomes.columns:
            scatter_df = outcomes.dropna(subset=["pnl_pct", "days_held"]).copy()
            if not scatter_df.empty:
                scatter_df["status_color"] = scatter_df.get("status2", scatter_df.get("status", "unknown"))
                fig = scatter(scatter_df, "days_held", "pnl_pct",
                              color="status_color",
                              title="P&L vs Days Held",
                              height=350)
                fig.update_xaxes(title="Days Held")
                fig.update_yaxes(title="P&L %")
                st.plotly_chart(fig, width='stretch')

    # ── Trigger Waiting Timeline ───────────────────────────────────────────
    if not outcomes.empty:
        st.markdown('<div class="section-divider">Trigger & Waiting Timeline</div>',
                    unsafe_allow_html=True)

        # Compute days_to_trigger from available data
        trigger_data = outcomes[
            outcomes["status"].isin(["triggered", "stopped_out", "target_hit"])
        ].copy()

        if not trigger_data.empty and "screen_date" in trigger_data.columns:
            if "triggered_at" in trigger_data.columns:
                trigger_data["trigger_date_parsed"] = pd.to_datetime(
                    trigger_data["triggered_at"], errors="coerce"
                )
                trigger_data["screen_date_parsed"] = pd.to_datetime(
                    trigger_data["screen_date"], errors="coerce"
                )
                trigger_data["days_to_trigger"] = (
                    trigger_data["trigger_date_parsed"]
                    - trigger_data["screen_date_parsed"]
                ).dt.days

            col_x, col_y = st.columns(2)
            with col_x:
                if "days_to_trigger" in trigger_data.columns:
                    fig = histogram(
                        trigger_data.dropna(subset=["days_to_trigger"]),
                        "days_to_trigger", "Days to Trigger Entry",
                        nbins=10, height=300,
                    )
                    fig.update_xaxes(title="Trading Days")
                    st.plotly_chart(fig, width='stretch')

            with col_y:
                expired_count = len(outcomes[outcomes["status"] == "expired"])
                triggered_count = len(trigger_data)
                mix_df = pd.DataFrame({
                    "Outcome": ["Triggered", "Expired"],
                    "Count": [triggered_count, expired_count],
                })
                fig = px.pie(
                    mix_df, names="Outcome", values="Count",
                    title="Trigger Rate", template=DARK_THEME,
                    color="Outcome",
                    color_discrete_map={
                        "Triggered": "#6366f1", "Expired": "#64748b",
                    },
                )
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(
                    height=300, showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#94a3b8", size=11),
                    margin=dict(l=20, r=20, t=40, b=20),
                )
                st.plotly_chart(fig, width='stretch')

            # Holding duration histogram
            if "days_held" in outcomes.columns:
                fig = histogram(
                    outcomes.dropna(subset=["days_held"]), "days_held",
                    "Days Held (Closed Trades)", nbins=15, height=300,
                )
                fig.update_xaxes(title="Days")
                st.plotly_chart(fig, width='stretch')

    # ── Recent Picks with tabs ─────────────────────────────────────────────
    if not screens.empty:
        st.markdown('<div class="section-divider">Recent Picks</div>',
                    unsafe_allow_html=True)

        cols_display = [c for c in ["screen_date", "symbol", "setup_type", "entry", "stop",
                                     "target", "risk_pct", "reward_risk_ratio", "score",
                                     "shares_suggested", "regime_tier", "is_override"]
                        if c in screens.columns]
        if len(cols_display) >= 2:
            display = screens[cols_display].head(50).copy()
            renames = {
                "screen_date": "Date", "symbol": "Symbol", "setup_type": "Setup",
                "entry": "Entry", "stop": "Stop", "target": "Target",
                "risk_pct": "Risk%", "reward_risk_ratio": "R:R",
                "score": "Score", "shares_suggested": "Shares",
                "regime_tier": "Regime", "is_override": "Override",
            }
            display = display.rename(
                columns={k: v for k, v in renames.items() if k in display.columns}
            )
            st.dataframe(display, width='stretch', hide_index=True)

        tab1, tab2, tab3 = st.tabs(["By Sector", "Score Distribution", "By Regime"])
        with tab1:
            pick_syms = screens[screens["symbol"].notna()]["symbol"].unique()
            if not constituents.empty:
                sec_map = dict(zip(constituents["symbol"], constituents["sector"]))
                sectors = pd.Series([sec_map.get(s, "Unknown") for s in pick_syms])
                counts = sectors.value_counts().reset_index()
                counts.columns = ["Sector", "Picks"]
                fig = px.pie(
                    counts, names="Sector", values="Picks",
                    title="Picks by Sector", template=DARK_THEME,
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(
                    height=400, showlegend=True,
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#94a3b8", size=11),
                    margin=dict(l=20, r=20, t=40, b=20),
                    legend=dict(font=dict(size=10)),
                )
                st.plotly_chart(fig, width='stretch')
            else:
                st.caption("No constituent sector data available.")

        with tab2:
            if "score" in screens.columns:
                fig = histogram(
                    screens.dropna(subset=["score"]), "score",
                    "Pick Score Distribution", nbins=30, height=350,
                )
                fig.update_xaxes(title="Score")
                st.plotly_chart(fig, width='stretch')
            else:
                st.caption("No score data available.")

        with tab3:
            if "regime_tier" in screens.columns:
                regime_counts = screens["regime_tier"].value_counts().reset_index()
                regime_counts.columns = ["Regime", "Picks"]
                fig = bar_chart(regime_counts, "Regime", "Picks",
                                title="Picks by Market Regime")
                st.plotly_chart(fig, width='stretch')
            else:
                st.caption("No regime tier data available.")

    # ── Trade Outcomes table ───────────────────────────────────────────────
    if not outcomes.empty:
        st.markdown('<div class="section-divider">Trade Outcomes</div>',
                    unsafe_allow_html=True)

        display_cols = [c for c in ["screen_date", "symbol", "setup_type", "status",
                                     "pnl_pct", "days_held", "market_regime"]
                        if c in outcomes.columns]
        if len(display_cols) >= 2:
            outcomes_display = outcomes[display_cols].head(50).copy()
            renames = {
                "screen_date": "Date", "symbol": "Symbol", "setup_type": "Setup",
                "status": "Status", "pnl_pct": "P&L%",
                "days_held": "Days Held", "market_regime": "Regime",
            }
            outcomes_display = outcomes_display.rename(
                columns={k: v for k, v in renames.items() if k in outcomes_display.columns}
            )
            st.dataframe(outcomes_display, width='stretch', hide_index=True)

    st.caption("Data refreshes every 5 minutes.")
