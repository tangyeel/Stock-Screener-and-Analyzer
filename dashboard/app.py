"""Main dashboard entry point — sidebar nav + dark theme + page router."""

import sys
sys.path.insert(0, "G:\\Stock Screener")

import streamlit as st
from datetime import datetime

PAGES = {
    "📈 Pipeline":        "screener",
    "🤖 Analysis Bot":    "bot",
    "🔬 Backtesting":     "backtest",
}

st.set_page_config(
    page_title="Stock Screener Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global dark-theme CSS ──────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

    :root {
        --bg-primary: #0a0e1a;
        --bg-secondary: #111827;
        --bg-card: #1a2332;
        --bg-card-hover: #1e293b;
        --border: rgba(255,255,255,0.06);
        --border-hover: rgba(255,255,255,0.12);
        --text-primary: #f1f5f9;
        --text-secondary: #94a3b8;
        --text-muted: #64748b;
        --accent-green: #22c55e;
        --accent-green-dim: rgba(34,197,94,0.12);
        --accent-red: #ef4444;
        --accent-red-dim: rgba(239,68,68,0.12);
        --accent-amber: #eab308;
        --accent-indigo: #6366f1;
    }

    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.15); }

    .stApp {
        background: linear-gradient(160deg, #0a0e1a 0%, #0f172a 50%, #0a0e1a 100%);
    }

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #111827 0%, #0a0e1a 100%);
        border-right: 1px solid var(--border);
    }

    section[data-testid="stSidebar"] .stRadio > div {
        gap: 3px;
        padding: 0 8px;
    }

    section[data-testid="stSidebar"] .stRadio label {
        display: flex;
        align-items: center;
        background: transparent;
        border-radius: 10px;
        padding: 10px 14px;
        transition: all 0.2s ease;
        border: 1px solid transparent;
        color: var(--text-secondary);
        font-weight: 500;
        font-size: 0.9rem;
    }

    section[data-testid="stSidebar"] .stRadio label:hover {
        background: rgba(255,255,255,0.03);
        border-color: var(--border-hover);
        color: var(--text-primary);
        transform: translateX(2px);
    }

    section[data-testid="stSidebar"] .stRadio label[data-selected="true"] {
        background: linear-gradient(135deg, var(--accent-green-dim), rgba(34,197,94,0.04));
        border-color: rgba(34,197,94,0.25);
        color: var(--accent-green);
        font-weight: 600;
        box-shadow: 0 0 20px rgba(34,197,94,0.05);
    }

    section[data-testid="stSidebar"] .stRadio label > div:first-child {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    /* Sidebar status badge */
    .sidebar-status {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 8px 14px;
        margin: 4px 8px;
        border-radius: 8px;
        font-size: 0.72rem;
        color: var(--text-muted);
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--border);
    }

    .sidebar-status .dot {
        width: 6px; height: 6px;
        border-radius: 50%;
        background: var(--accent-green);
        box-shadow: 0 0 6px rgba(34,197,94,0.4);
        animation: pulse 2s ease-in-out infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
    }

    /* ── Typography ── */
    h1, h2, h3, h4, h5, h6 {
        color: var(--text-primary) !important;
        letter-spacing: -0.02em;
    }

    h1 {
        font-size: 1.6rem !important;
        font-weight: 700 !important;
        background: linear-gradient(135deg, #f1f5f9 0%, #94a3b8 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 0.25rem !important;
    }

    h2 {
        font-size: 1.15rem !important;
        font-weight: 600 !important;
        margin-top: 1.5rem !important;
        margin-bottom: 0.75rem !important;
        color: #e2e8f0 !important;
    }

    h3 {
        font-size: 1rem !important;
        font-weight: 600 !important;
        color: #e2e8f0 !important;
    }

    /* ── DataFrames ── */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid var(--border);
        transition: border-color 0.2s;
    }

    .stDataFrame:hover {
        border-color: var(--border-hover);
    }

    .stDataFrame table { font-size: 0.8rem; }

    .stDataFrame thead tr th {
        background: #1e293b !important;
        color: var(--text-muted) !important;
        font-weight: 600 !important;
        font-size: 0.72rem !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        padding: 8px 14px !important;
        border-bottom: 1px solid var(--border) !important;
    }

    .stDataFrame tbody tr td {
        padding: 6px 14px !important;
        border-bottom: 1px solid rgba(255,255,255,0.03) !important;
        color: #cbd5e1 !important;
        font-size: 0.8rem !important;
    }

    .stDataFrame tbody tr:hover {
        background: rgba(255,255,255,0.03) !important;
    }

    .stDataFrame tbody tr:last-child td {
        border-bottom: none !important;
    }

    /* ── Metric cards ── */
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, var(--bg-card) 0%, #1a2332 100%);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        transition: all 0.2s ease;
    }

    [data-testid="stMetric"]:hover {
        border-color: var(--border-hover);
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }

    [data-testid="stMetricLabel"] {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    [data-testid="stMetricValue"] {
        color: var(--text-primary) !important;
        font-size: 1.5rem !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }

    [data-testid="stMetricDelta"] svg { display: none; }

    /* ── Select / Input ── */
    .stSelectbox, .stSelectbox > div > div {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        color: var(--text-primary) !important;
        transition: border-color 0.2s;
    }

    .stSelectbox:hover > div > div {
        border-color: var(--border-hover) !important;
    }

    .stSelectbox > label, .stCheckbox > label {
        color: var(--text-secondary) !important;
        font-weight: 500 !important;
        font-size: 0.8rem !important;
    }

    /* ── Expanders ── */
    .streamlit-expanderHeader {
        background: var(--bg-card) !important;
        border-radius: 10px !important;
        border: 1px solid var(--border) !important;
        color: var(--text-primary) !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
        transition: border-color 0.2s;
    }

    .streamlit-expanderHeader:hover {
        border-color: var(--border-hover) !important;
    }

    .streamlit-expanderContent {
        border: 1px solid var(--border) !important;
        border-top: none !important;
        border-radius: 0 0 10px 10px !important;
        padding: 16px !important;
        background: rgba(30,41,59,0.4) !important;
    }

    /* ── Plotly ── */
    .js-plotly-plot, .plot-container {
        border-radius: 12px;
        overflow: hidden;
    }

    /* ── Alerts ── */
    .stAlert {
        border-radius: 10px !important;
        border: 1px solid var(--border) !important;
        background: var(--bg-card) !important;
        color: var(--text-secondary) !important;
    }

    .stAlert [data-testid="stMarkdownContainer"] p {
        font-size: 0.85rem;
    }

    /* ── Buttons ── */
    .stDownloadButton button {
        background: linear-gradient(135deg, var(--accent-green) 0%, #16a34a 100%) !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        border-radius: 10px !important;
        padding: 6px 20px !important;
        transition: all 0.2s !important;
        font-size: 0.8rem !important;
    }

    .stDownloadButton button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 16px rgba(34,197,94,0.3);
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background: transparent;
        border-bottom: 1px solid var(--border);
    }

    .stTabs [data-baseweb="tab"] {
        background: transparent;
        color: var(--text-muted);
        border-radius: 8px 8px 0 0;
        padding: 8px 18px;
        font-weight: 500;
        font-size: 0.82rem;
        transition: all 0.15s;
        border-bottom: 2px solid transparent;
    }

    .stTabs [data-baseweb="tab"]:hover {
        color: var(--text-secondary);
        background: rgba(255,255,255,0.02);
    }

    .stTabs [aria-selected="true"] {
        color: var(--accent-green) !important;
        border-bottom: 2px solid var(--accent-green) !important;
        background: transparent !important;
    }

    /* ── Page fade-in ── */
    .main > .block-container {
        animation: fadeIn 0.4s ease;
    }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0); }
    }

    /* ── Dividers ── */
    hr {
        border-color: var(--border) !important;
        margin: 1.5rem 0 !important;
    }

    /* ── Captions ── */
    .stCaption, .element-container + div > small, .caption-refresh {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
    }

    footer { visibility: hidden; }

    /* ── KPI card HTML component ── */
    .kpi-card {
        background: linear-gradient(135deg, var(--bg-card) 0%, #1a2332 100%);
        border-radius: 12px;
        padding: 18px 14px;
        text-align: center;
        border: 1px solid var(--border);
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        transition: all 0.2s ease;
    }

    .kpi-card:hover {
        border-color: var(--border-hover);
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }

    .kpi-card .label {
        color: var(--text-muted);
        font-size: 0.7rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 6px;
    }

    .kpi-card .value {
        font-size: 1.5rem;
        font-weight: 700;
        letter-spacing: -0.02em;
    }

    .kpi-card .delta {
        font-size: 0.75rem;
        color: var(--text-muted);
        margin-top: 4px;
    }

    /* ── Section divider with label ── */
    .section-divider {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 1.5rem 0 1rem;
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text-secondary);
        letter-spacing: 0.02em;
        text-transform: uppercase;
    }

    .section-divider::after {
        content: '';
        flex: 1;
        height: 1px;
        background: linear-gradient(90deg, var(--border) 0%, transparent 100%);
    }

    /* ── Live trade table status bar ── */
    .status-bar-bg {
        width: 100%;
        background: #1e293b;
        border-radius: 4px;
        height: 6px;
        margin-top: 4px;
        overflow: hidden;
    }
    .status-bar-fill {
        height: 6px;
        border-radius: 4px;
        transition: width 0.5s ease;
    }

    /* ── Caption badge for refresh times ── */
    .caption-refresh {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 2px 10px;
        border-radius: 6px;
        background: rgba(255,255,255,0.02);
        border: 1px solid var(--border);
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────
st.sidebar.markdown("""
<div style="text-align:center;padding:20px 0 12px">
    <div style="font-size:2rem;margin-bottom:4px">📊</div>
    <div style="font-weight:700;font-size:1.1rem;color:var(--text-primary);
                letter-spacing:-0.02em">Stock Screener</div>
    <div style="font-size:0.68rem;color:var(--text-muted);margin-top:2px">v2 — Dashboard</div>
</div>
""", unsafe_allow_html=True)

st.sidebar.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)

selected = st.sidebar.radio(
    "Navigation",
    list(PAGES.keys()),
    label_visibility="collapsed",
)

# ── Sidebar system status ─────────────────────────────────────────────────
st.sidebar.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
st.sidebar.markdown("""
<div class="sidebar-status">
    <span class="dot"></span>
    <span>System Online</span>
</div>
""", unsafe_allow_html=True)

try:
    from db.database import get_connection
    with get_connection() as conn:
        last_run = conn.execute(
            "SELECT run_date FROM daily_run_log ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
    if last_run:
        st.sidebar.markdown(
            f'<div class="sidebar-status" style="font-size:0.68rem">'
            f'Last pipeline: {last_run["run_date"]}</div>',
            unsafe_allow_html=True,
        )
except Exception:
    pass

st.sidebar.markdown(
    "<div style='font-size:0.68rem;color:var(--text-muted);text-align:center;"
    "padding:12px 0 8px'>Data cached · auto-refresh 5 min</div>",
    unsafe_allow_html=True,
)

# ── Page router ────────────────────────────────────────────────────────────
page_module = PAGES[selected]
__import__(f"dashboard.pages.{page_module}", fromlist=["render"])
mod = sys.modules[f"dashboard.pages.{page_module}"]
mod.render()
