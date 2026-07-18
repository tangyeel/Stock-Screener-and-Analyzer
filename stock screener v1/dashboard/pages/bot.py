"""Analysis bot metrics page — queries, latency, resolution, verdicts."""

import streamlit as st
import pandas as pd
import plotly.express as px

from dashboard.data.queries import q_query_log, q_resolution_log, q_analysis_log
from dashboard.charts.plots import (
    kpi_card, bar_chart, line_chart, histogram, DARK_THEME,
)
from dashboard.utils import fmt_pct, fmt_int, color_for_metric


def _latency_color(ms: float) -> str:
    if ms < 500:
        return "#22c55e"
    if ms < 1500:
        return "#eab308"
    return "#ef4444"


def render():
    col_t, col_b = st.columns([3, 1])
    with col_t:
        st.title("Telegram Bot Analytics")
    with col_b:
        if st.button("🔄 Refresh Bot Logs"):
            q_query_log.clear()
            q_resolution_log.clear()
            q_analysis_log.clear()
            st.rerun()

    queries = q_query_log()
    resolutions = q_resolution_log()
    analyses = q_analysis_log()

    if queries.empty:
        st.info("No bot query data yet. Start the bot and send it some queries.")
        return

    # ── KPI row ─────────────────────────────────────────────────────────────
    total = len(queries)
    success = len(queries[queries.get("status") == "success"])
    errors = len(queries[queries.get("status") == "error"])
    avg_latency = queries["response_time_ms"].mean()
    success_rate = safe_div(success, total, default=0)

    cols = st.columns(4)
    with cols[0]:
        st.markdown(kpi_card("Total Queries", fmt_int(total),
                             "#6366f1"), unsafe_allow_html=True)
    with cols[1]:
        st.markdown(kpi_card("Success Rate", f"{success_rate*100:.0f}%",
                             "#22c55e" if success_rate > 0.9 else "#eab308"),
                    unsafe_allow_html=True)
    with cols[2]:
        st.markdown(kpi_card("Errors", fmt_int(errors),
                             "#ef4444"), unsafe_allow_html=True)
    with cols[3]:
        lat_color = _latency_color(avg_latency) if avg_latency else "#64748b"
        st.markdown(kpi_card("Avg Latency",
                             f"{avg_latency:.0f} ms" if avg_latency else "—",
                             lat_color),
                    unsafe_allow_html=True)

    # ── Two-column: Volume + Latency distribution ──────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Query Volume")
        daily = queries.copy()
        daily["date"] = pd.to_datetime(daily["created_at"]).dt.date
        daily_counts = daily.groupby("date").size().reset_index(name="count")
        fig = line_chart(daily_counts, "date", "count", "Queries per Day", height=280)
        st.plotly_chart(fig, width='stretch')

    with col2:
        st.subheader("Response Time Distribution")
        if "response_time_ms" in queries.columns:
            fig = histogram(queries.dropna(subset=["response_time_ms"]),
                            "response_time_ms", "Response Time (ms)", nbins=30, height=280)
            fig.update_xaxes(title="ms")
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No latency data.")

    # ── Two-column: Latency by type + Popular instruments ──────────────────
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Latency by Instrument Type")
        if ("instrument_type" in queries.columns
                and queries["instrument_type"].notna().any()):
            fig = px.box(
                queries.dropna(subset=["instrument_type", "response_time_ms"]),
                x="instrument_type", y="response_time_ms",
                title=None, template=DARK_THEME,
                color="instrument_type",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(
                height=320, showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#1a2332",
                font=dict(color="#94a3b8", size=11),
                margin=dict(l=40, r=20, t=30, b=50),
                hoverlabel=dict(bgcolor="#1e293b", font_size=12, font_color="#f1f5f9"),
            )
            fig.update_xaxes(title="")
            fig.update_yaxes(title="ms")
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No instrument-type data.")

    with col2:
        st.subheader("Most Requested Instruments")
        if "resolved_ticker" in queries.columns:
            top_syms = queries["resolved_ticker"].value_counts().head(20).reset_index()
            top_syms.columns = ["Ticker", "Queries"]
            fig = bar_chart(top_syms, "Ticker", "Queries",
                            title="Top 20 Requested Tickers", horizontal=True, height=480)
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No instrument-resolution data.")

    # ── Two-column: Resolution methods + Verdicts ──────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if not resolutions.empty and "method" in resolutions.columns:
            st.subheader("Resolution Methods")
            method_counts = resolutions["method"].value_counts().reset_index()
            method_counts.columns = ["Method", "Count"]
            fig = px.pie(
                method_counts, names="Method", values="Count",
                title=None, template=DARK_THEME,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(
                height=320, showlegend=True,
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94a3b8", size=11),
                margin=dict(l=20, r=20, t=20, b=40),
                legend=dict(font=dict(size=10)),
            )
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No resolution data.")

    with col2:
        if not analyses.empty and "verdict" in analyses.columns:
            st.subheader("Analysis Verdicts")
            verdict_counts = analyses["verdict"].value_counts().reset_index()
            verdict_counts.columns = ["Verdict", "Count"]
            fig = bar_chart(verdict_counts, "Verdict", "Count", title=None)
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption("No analysis data.")

    # ── Recent queries table ───────────────────────────────────────────────
    st.subheader("Recent Queries")
    display_cols = [c for c in ["created_at", "raw_query", "resolved_ticker",
                                 "instrument_type", "verdict", "composite_score",
                                 "response_time_ms", "status"]
                    if c in queries.columns]
    if len(display_cols) >= 2:
        display = queries[display_cols].head(100).copy()
        renames = {
            "created_at": "Time", "raw_query": "Query", "resolved_ticker": "Resolved",
            "instrument_type": "Type", "verdict": "Verdict", "composite_score": "Score",
            "response_time_ms": "Latency(ms)", "status": "Status",
        }
        display = display.rename(
            columns={k: v for k, v in renames.items() if k in display.columns}
        )
        st.dataframe(display, width='stretch', hide_index=True)

    st.caption("Data refreshes every 5 minutes.")


def safe_div(a, b, default=0):
    if a is None or b is None or b == 0:
        return default
    return a / b
