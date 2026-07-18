"""Autoresearch dashboard page — visualizes LLM strategy trials, shadow scores, and overfitting checks."""

import os
import sys
import json
import time
import subprocess
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from dashboard.charts.plots import kpi_card, DARK_THEME

STATUS_COLORS = {
    "accepted": "#22c55e",      # Green
    "discarded": "#ef4444",     # Red
    "look-ahead": "#f97316",    # Orange (Causal Reject / Look-ahead)
    "overfit": "#f97316",       # Orange
    "failed": "#64748b",        # Gray
}

STATUS_BADGES = {
    s: f'<span style="display:inline-block;padding:2px 10px;border-radius:10px;'
       f'font-size:0.72rem;font-weight:600;background:{c}22;color:{c};'
       f'border:1px solid {c}44">{s.replace("_"," ").title()}</span>'
    for s, c in STATUS_COLORS.items()
}

STATUS_FILE = "G:\\Stock Screener\\run_logging\\autoresearch_status.json"
LOG_FILE = "G:\\Stock Screener\\run_logging\\autoresearch_run.log"

def load_autoresearch_history():
    history_file = "G:\\Stock Screener\\autoresearch_history.json"
    if not os.path.exists(history_file):
        return []
    try:
        with open(history_file, "r") as f:
            return json.load(f)
    except Exception:
        return []

def get_runner_status():
    if not os.path.exists(STATUS_FILE):
        return {"is_running": False, "current_iteration": 0, "total_iterations": 0}
    try:
        with open(STATUS_FILE, "r") as f:
            data = json.load(f)
            # Verify if process PID is actually alive
            pid = data.get("pid")
            if pid and data.get("is_running"):
                try:
                    # Windows process check
                    res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
                    if str(pid) not in res.stdout:
                        data["is_running"] = False
                except Exception:
                    pass
            return data
    except Exception:
        return {"is_running": False, "current_iteration": 0, "total_iterations": 0}

def read_log_tail(num_chars=3000):
    if not os.path.exists(LOG_FILE):
        return ""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            return content[-num_chars:]
    except Exception:
        return ""

def render():
    status = get_runner_status()
    is_running = status.get("is_running", False)

    col_t, col_inp, col_btn = st.columns([2, 1, 1])
    with col_t:
        st.title("Autonomous Strategy Researcher")
    with col_inp:
        num_runs = st.number_input("Iterations to Run", min_value=1, max_value=50, value=5, disabled=is_running)
    with col_btn:
        st.write("") # vertical alignment spacer
        run_requested = st.button("🚀 Start Research Loop", disabled=is_running)

    os.makedirs("run_logging", exist_ok=True)

    if run_requested:
        if is_running:
            st.warning("An optimization run is already active! Monitoring progress below.")
        else:
            log_handle = open(LOG_FILE, "w", encoding="utf-8")
            subprocess.Popen(
                [sys.executable, "-u", "autoresearch_loop.py", "--iterations", str(num_runs)],
                cwd="G:\\Stock Screener",
                stdout=log_handle,
                stderr=log_handle,
            )
            st.info(f"Launched {num_runs} research iteration(s) in background!")
            time.sleep(1)
            st.rerun()

    # ── Live Progress Section (Persistent across refreshes) ────────────────────
    if is_running:
        curr_iter = status.get("current_iteration", 1)
        tot_iter = status.get("total_iterations", num_runs)
        pct = min(curr_iter / max(tot_iter, 1), 1.0)
        
        st.markdown(f"**⚡ AI Quant Researching... (Iteration {curr_iter} of {tot_iter})**")
        st.progress(pct)

        with st.expander("📺 Live Console Log (Auto-Updating)", expanded=True):
            st.code(read_log_tail(), language="plaintext")

        st.caption("Page automatically refreshes every 3 seconds while active...")
        time.sleep(3)
        st.rerun()

    history = load_autoresearch_history()

    if not history:
        st.info("No strategy search history found yet. Start the self-improvement loop above.")
        return

    df = pd.DataFrame(history)

    # ── KPI Row ──────────────────────────────────────────────────────────────
    gen_count = len(df)
    
    accepted_df = df[df["status"] == "accepted"]
    best_score = accepted_df["score"].max() if not accepted_df.empty else 0.0
    
    causal_rejects = len(df[df["status"].isin(["overfit", "look-ahead"])])
    failed_runs = len(df[df["status"] == "failed"])

    cols = st.columns(4)
    with cols[0]:
        st.markdown(kpi_card("Gen Count", str(gen_count), "#6366f1"), unsafe_allow_html=True)
    with cols[1]:
        st.markdown(kpi_card("Best Score", f"{best_score:.4f}", "#22c55e"), unsafe_allow_html=True)
    with cols[2]:
        st.markdown(kpi_card("Causal Rejects", str(causal_rejects), "#f97316"), unsafe_allow_html=True)
    with cols[3]:
        st.markdown(kpi_card("Failed Runs", str(failed_runs), "#64748b"), unsafe_allow_html=True)

    # ── Best Strategy Performance Block ──────────────────────────────────────
    if not accepted_df.empty:
        best_run = accepted_df.loc[accepted_df["score"].idxmax()]
        
        st.markdown(f"""
        <div style="background:linear-gradient(135deg, rgba(34,197,94,0.06) 0%, rgba(15,23,42,0.6) 100%);
                    border:1px solid rgba(34,197,94,0.2); border-radius:12px; padding:20px; margin:20px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <span style="font-weight:700; color:#22c55e; font-size:0.95rem;">🏆 Best Strategy Performance (Train Backtest)</span>
                <span style="font-size:0.8rem; color:#64748b; font-weight:600;">Gen {best_run['generation']}</span>
            </div>
            <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:16px;">
                <div>
                    <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase;">Total Return</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#22c55e;">{best_run.get('pnl_pct', 0):+.2f}%</div>
                </div>
                <div>
                    <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase;">Win Rate</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#cbd5e1;">{best_run.get('win_rate', 0):.1f}%</div>
                </div>
                <div>
                    <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase;">Max Drawdown</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#ef4444;">-{abs(best_run.get('max_drawdown_pct', 0)):.2f}%</div>
                </div>
                <div>
                    <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase;">Trades</div>
                    <div style="font-size:1.3rem; font-weight:700; color:#6366f1;">{int(best_run.get('trades_count', 0))}</div>
                </div>
            </div>
            <div style="margin-top:14px; font-size:0.82rem; color:#94a3b8; font-style:italic;">
                &ldquo; {best_run.get('strategy_notes', '')} &rdquo;
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ── Subtitle Status bar ──────────────────────────────────────────────────
    st.markdown(f"""
    <div style="display:flex; align-items:center; gap:8px; margin: 15px 0; font-size:0.82rem; color:#94a3b8;">
        <span style="width:8px; height:8px; border-radius:50%; background:#22c55e; 
                     box-shadow:0 0 6px #22c55e; display:inline-block;"></span>
        <span>Researcher Active &middot; Gen {gen_count} &middot; Training on 2025 data &middot; 2026 held out as test set</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Shadow Score Progression Chart ───────────────────────────────────────
    st.subheader("Shadow Score Progression")
    
    running_max = []
    current_max = -9999.0
    for idx, row in df.iterrows():
        if row["status"] == "accepted":
            current_max = max(current_max, row["score"])
        running_max.append(current_max if current_max != -9999.0 else None)
    
    df["running_max"] = running_max

    fig = go.Figure()

    if not df["running_max"].isna().all():
        fig.add_trace(go.Scatter(
            x=df["generation"], y=df["running_max"],
            mode="lines", name="Best so far",
            line=dict(color="#22c55e", width=1.5, dash="dash"),
            hovertemplate="Best Fitness: %{y:.4f}<extra></extra>"
        ))

    for status_key, color in STATUS_COLORS.items():
        sub_df = df[df["status"] == status_key]
        if sub_df.empty:
            continue
        
        fig.add_trace(go.Scatter(
            x=sub_df["generation"], y=sub_df["score"],
            mode="markers", name=status_key.replace("-", " ").title(),
            marker=dict(color=color, size=9, line=dict(width=0.5, color="#1a2332")),
            hovertemplate="Gen %{x}<br>Fitness: %{y:.4f}<extra></extra>"
        ))

    fig.update_layout(
        template=DARK_THEME,
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#1a2332",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis=dict(title="Generation", gridcolor="rgba(255,255,255,0.04)"),
        yaxis=dict(title="Fitness Score", gridcolor="rgba(255,255,255,0.04)"),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            bgcolor="rgba(0,0,0,0)"
        )
    )
    st.plotly_chart(fig, width='stretch')

    # ── Generations History Table ───────────────────────────────────────────
    st.subheader("Generation History")
    
    rows = []
    for _, r in df.iloc[::-1].iterrows():
        status_val = r.get("status", "discarded")
        score_val = r.get("score", 0.0)
        
        score_str = f"{score_val:.4f}" if status_val != "failed" else "—"
        
        rows.append({
            "Gen": f"Gen {int(r['generation'])}",
            "Score": score_str,
            "PnL %": f"{r.get('pnl_pct', 0):+.2f}%" if status_val != "failed" else "—",
            "Trades": str(int(r.get('trades_count', 0))) if status_val != "failed" else "—",
            "Win %": f"{r.get('win_rate', 0):.1f}%" if status_val != "failed" else "—",
            "DD": f"-{abs(r.get('max_drawdown_pct', 0)):.1f}%" if status_val != "failed" else "—",
            "Status": STATUS_BADGES.get(status_val, status_val),
            "Strategy / Notes": r.get("strategy_notes", ""),
        })

    history_display = pd.DataFrame(rows)
    st.markdown(
        history_display.to_html(escape=False, index=False)
        .replace('<table', '<table style="width:100%;font-size:0.82rem;border-collapse:collapse;"')
        .replace('<td>', '<td style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.03);">')
        .replace('<th>', '<th style="padding:12px 14px;background:#1e293b;color:#64748b;text-align:left;font-size:0.75rem;text-transform:uppercase;">'),
        unsafe_allow_html=True,
    )
