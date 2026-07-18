"""All SQL queries, cached via @st.cache_data."""

import sys
sys.path.insert(0, "G:\\Stock Screener")

import streamlit as st
import pandas as pd
from db.database import get_connection


# ── Screener queries ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def q_run_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM daily_run_log ORDER BY run_date DESC LIMIT 100"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_daily_screens() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM daily_screens ORDER BY screen_date DESC, score DESC"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_regime_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM regime_log ORDER BY run_date DESC"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_trade_outcomes() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT t.*, s.screen_date, s.symbol, s.setup_type, s.score, s.market_regime, s.regime_tier
               FROM trade_outcomes t
               JOIN daily_screens s ON t.screen_id = s.id
               ORDER BY t.updated_at DESC"""
        ).fetchall()
    return _to_df(rows)


# ── Bot queries ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=10)
def q_query_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM query_log ORDER BY created_at DESC LIMIT 5000"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=10)
def q_resolution_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM resolution_log ORDER BY created_at DESC LIMIT 5000"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=10)
def q_analysis_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM analysis_log ORDER BY created_at DESC LIMIT 10000"""
        ).fetchall()
    return _to_df(rows)


# ── Backtest queries ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def q_backtest_runs() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.*, m.total_signals, m.total_triggered 
               FROM backtest_runs r
               LEFT JOIN backtest_metrics m ON r.id = m.backtest_run_id
               ORDER BY r.created_at DESC"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_backtest_trades(run_id: str) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM backtest_trades WHERE backtest_run_id = ? ORDER BY signal_date""",
            (run_id,),
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_backtest_metrics(run_id: str) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM backtest_metrics WHERE backtest_run_id = ?""",
            (run_id,),
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_backtest_walkforward(run_id: str) -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM backtest_walkforward_windows
               WHERE backtest_run_id = ? ORDER BY window_index""",
            (run_id,),
        ).fetchall()
    return _to_df(rows)


# ── Constituents ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def q_constituents() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM nifty100_constituents ORDER BY symbol"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_open_trades() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT t.*, s.screen_date, s.symbol, s.setup_type, s.entry, s.stop,
                      s.target, s.score, s.market_regime, s.regime_tier
               FROM trade_outcomes t
               JOIN daily_screens s ON t.screen_id = s.id
               WHERE t.status IN ('pending', 'triggered')
               ORDER BY t.updated_at DESC"""
        ).fetchall()
    return _to_df(rows)


@st.cache_data(ttl=300)
def q_ingestion_log() -> pd.DataFrame:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM ingestion_log ORDER BY created_at DESC LIMIT 2000"""
        ).fetchall()
    return _to_df(rows)


# ── Helper ────────────────────────────────────────────────────────────────────

def _to_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])
