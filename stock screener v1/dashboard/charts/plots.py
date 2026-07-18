"""Reusable Plotly chart builders with professional dark-theme styling."""

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

# ── Theme palette ──────────────────────────────────────────────────────────
DARK_THEME = "plotly_dark"

BG_PLOT = "#1a2332"
BG_PAPER = "rgba(0,0,0,0)"
TEXT_COLOR = "#94a3b8"
GRID_COLOR = "rgba(255,255,255,0.04)"
AXIS_COLOR = "#64748b"
FONT_FAMILY = "Inter, -apple-system, BlinkMacSystemFont, sans-serif"
MONO_FONT = "JetBrains Mono, monospace"

COLORS = px.colors.qualitative.Set2

_COLOR_SEQ = ["#22c55e", "#6366f1", "#eab308", "#f97316", "#ef4444",
              "#06b6d4", "#a855f7", "#ec4899"]

_REGIME_COLORS = {
    "strong_bull": "rgba(34,197,94,0.08)",
    "neutral_selective": "rgba(234,179,8,0.06)",
    "weak_selective": "rgba(239,68,68,0.06)",
    "bearish": "rgba(239,68,68,0.12)",
}


def _base_layout(title: str = "", height: int = 350, **kw) -> dict:
    """Standard layout for all charts."""
    return dict(
        title=dict(
            text=title,
            font=dict(size=13, color=TEXT_COLOR, family=FONT_FAMILY),
            x=0, xanchor="left",
        ),
        template=DARK_THEME,
        height=height,
        paper_bgcolor=BG_PAPER,
        plot_bgcolor=BG_PLOT,
        font=dict(family=FONT_FAMILY, size=11, color=TEXT_COLOR),
        margin=dict(l=50, r=20, t=50, b=50),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#1e293b",
            font_size=12,
            font_color="#f1f5f9",
            font_family=FONT_FAMILY,
            bordercolor="rgba(255,255,255,0.1)",
        ),
        xaxis=dict(
            gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR,
            tickfont=dict(size=10, color=AXIS_COLOR, family=FONT_FAMILY),
            showline=False,
        ),
        yaxis=dict(
            gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR,
            tickfont=dict(size=10, color=AXIS_COLOR, family=FONT_FAMILY),
            showline=False,
        ),
        legend=dict(
            font=dict(size=10, color=TEXT_COLOR, family=FONT_FAMILY),
            bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        ),
        **kw,
    )


# ── KPI card (HTML, for use with st.markdown) ──────────────────────────────
def kpi_card(title: str, value: str, color: str = "#22c55e", delta: str = None):
    """Return a professional HTML KPI card fragment."""
    delta_html = (
        f'<div class="delta">{delta}</div>'
        if delta else ""
    )
    return f"""
    <div class="kpi-card">
        <div class="label">{title}</div>
        <div class="value" style="color:{color}">{value}</div>
        {delta_html}
    </div>
    """


# ── Cumulative return helper ───────────────────────────────────────────────
def _cumulative_pct(df: pd.DataFrame, value_col: str, capital: float = 500_000) -> pd.Series:
    """Compute cumulative portfolio return (accounting for position sizing)."""
    if "position_entry_value" in df.columns:
        curve = [capital]
        for _, r in df.iterrows():
            pnl_rupee = (r.get(value_col) or 0) / 100.0 * (r.get("position_entry_value") or 0)
            curve.append(curve[-1] + pnl_rupee)
        return pd.Series(curve[1:], index=df.index) / capital
    return (1 + df[value_col].fillna(0) / 100).cumprod()


# ── Equity curve ───────────────────────────────────────────────────────────
def equity_curve(df: pd.DataFrame, date_col: str = "exit_date",
                 value_col: str = "pnl_pct", benchmark: pd.Series = None,
                 title: str = "Equity Curve") -> go.Figure:
    if df.empty:
        return go.Figure()
    df = df.sort_values(date_col).reset_index(drop=True)
    cumulative = _cumulative_pct(df, value_col)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[date_col], y=cumulative, mode="lines",
        name="Strategy",
        line=dict(color="#22c55e", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(34,197,94,0.06)",
        hovertemplate="Date: %{x}<br>Value: %{y:.4f}<extra>Strategy</extra>",
    ))
    if benchmark is not None:
        fig.add_trace(go.Scatter(
            x=df[date_col], y=benchmark, mode="lines",
            name="Nifty 100",
            line=dict(color="#6366f1", width=2, dash="dot"),
            hovertemplate="Date: %{x}<br>Value: %{y:.4f}<extra>Nifty 100</extra>",
        ))
    final_val = cumulative.iloc[-1] if len(cumulative) > 0 else 1
    fig.add_annotation(
        x=1, y=final_val,
        text=f"{(final_val-1)*100:+.2f}%",
        showarrow=False,
        xref="paper", yref="y",
        font=dict(size=12, color="#22c55e" if final_val >= 1 else "#ef4444",
                  family=MONO_FONT),
        bgcolor="#1e293b", bordercolor="rgba(255,255,255,0.1)",
        borderwidth=1, borderpad=4,
    )
    fig.update_layout(**_base_layout(title, 380, yaxis_title="Portfolio Value (₹)"))
    fig.add_hline(y=1, line_dash="dot", line_color="#64748b", opacity=0.3)
    fig.update_yaxes(tickformat=".2f")
    return fig


# ── Drawdown chart ─────────────────────────────────────────────────────────
def drawdown_chart(df: pd.DataFrame, date_col: str = "exit_date",
                   value_col: str = "pnl_pct", title: str = "Drawdown") -> go.Figure:
    if df.empty:
        return go.Figure()
    df = df.sort_values(date_col).reset_index(drop=True)
    cum = _cumulative_pct(df, value_col)
    running_max = cum.cummax()
    dd = (cum - running_max) / running_max * 100
    min_dd = dd.min()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[date_col], y=dd, mode="lines", name="Drawdown",
        line=dict(color="#ef4444", width=1.5),
        fill="tozeroy", fillcolor="rgba(239,68,68,0.08)",
        hovertemplate="Date: %{x}<br>DD: %{y:.2f}%<extra>Drawdown</extra>",
    ))
    fig.add_annotation(
        x=df[date_col].iloc[dd.idxmin()] if not dd.empty else df[date_col].iloc[0],
        y=min_dd,
        text=f"{min_dd:.1f}%",
        showarrow=True, arrowhead=2, arrowsize=1,
        arrowcolor="#ef4444",
        font=dict(size=11, color="#ef4444", family=MONO_FONT),
        bgcolor="#1e293b", bordercolor="rgba(239,68,68,0.3)",
        borderwidth=1, borderpad=4,
    )
    fig.update_layout(**_base_layout(title, 240, yaxis_title="Drawdown %"))
    fig.add_hline(y=0, line_dash="dot", line_color="#64748b", opacity=0.3)
    return fig


# ── Bar chart ──────────────────────────────────────────────────────────────
def bar_chart(df: pd.DataFrame, x: str, y: str, color_col: str = None,
              title: str = "", height: int = 400, horizontal: bool = False,
              color_map: dict = None) -> go.Figure:
    if df.empty:
        return go.Figure()
    if horizontal:
        x_ax, y_ax = y, x
    else:
        x_ax, y_ax = x, y
    fig = px.bar(
        df, x=x_ax, y=y_ax, color=color_col, title=title,
        template=DARK_THEME, height=height,
        text_auto=".1f" if df[y].dtype.kind == "f" else True,
        color_discrete_map=color_map,
        color_discrete_sequence=_COLOR_SEQ if not color_map else None,
    )
    fig.update_layout(**_base_layout(title, height))
    fig.update_traces(
        marker_line_width=0,
        textfont=dict(size=9, color=TEXT_COLOR, family=FONT_FAMILY),
        hovertemplate="%{x}<br>%{y}<extra></extra>",
    )
    if horizontal:
        fig.update_yaxes(categoryorder="total ascending")
    else:
        fig.update_xaxes(categoryorder="total ascending")
    fig.update(layout_showlegend=bool(color_col))
    return fig


# ── Scatter plot ───────────────────────────────────────────────────────────
def scatter(df: pd.DataFrame, x: str, y: str, color: str = None,
            size: str = None, title: str = "", height: int = 400,
            color_discrete_map: dict = None) -> go.Figure:
    if df.empty:
        return go.Figure()
    fig = px.scatter(
        df, x=x, y=y, color=color, size=size, title=title,
        template=DARK_THEME, height=height, hover_data=df.columns,
        color_discrete_sequence=_COLOR_SEQ,
        color_discrete_map=color_discrete_map,
        color_continuous_scale=["#ef4444", "#eab308", "#22c55e"],
    )
    fig.update_layout(**_base_layout(title, height))
    fig.update_traces(
        marker=dict(line=dict(width=0.5, color="#1e293b")),
        hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#64748b", opacity=0.3)
    return fig


# ── Histogram ──────────────────────────────────────────────────────────────
def histogram(df: pd.DataFrame, col: str, title: str = "", height: int = 300,
              nbins: int = 20, color: str = "#6366f1") -> go.Figure:
    if df.empty or col not in df.columns:
        return go.Figure()
    fig = px.histogram(
        df, x=col, nbins=nbins, title=title, template=DARK_THEME, height=height,
        color_discrete_sequence=[color],
    )
    fig.update_layout(**_base_layout(title, height))
    fig.update_traces(
        marker_line_width=0,
        marker=dict(
            line=dict(width=0.5, color="rgba(0,0,0,0.2)"),
        ),
        hovertemplate="%{x}<br>Count: %{y}<extra></extra>",
    )
    fig.update_yaxes(title="Count")
    return fig


# ── Line chart ─────────────────────────────────────────────────────────────
def line_chart(df: pd.DataFrame, x: str, y: str | list, title: str = "",
               height: int = 300) -> go.Figure:
    if df.empty:
        return go.Figure()
    if isinstance(y, str):
        y = [y]
    fig = px.line(
        df, x=x, y=y, title=title, template=DARK_THEME, height=height,
        color_discrete_sequence=_COLOR_SEQ,
    )
    fig.update_layout(**_base_layout(title, height))
    fig.update_traces(
        hovertemplate="%{x}<br>%{y}<extra></extra>",
    )
    return fig


# ── Regime overlay (equity curve with regime-colored background) ───────────
def regime_overlay(df: pd.DataFrame, trades: pd.DataFrame,
                   date_col: str = "exit_date", value_col: str = "pnl_pct") -> go.Figure:
    if trades.empty or df.empty:
        return go.Figure()
    trades = trades.sort_values(date_col).reset_index(drop=True)
    cum = _cumulative_pct(trades, value_col)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trades[date_col], y=cum, mode="lines",
        name="Strategy",
        line=dict(color="#22c55e", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(34,197,94,0.06)",
    ))
    for _, r in df.iterrows():
        fig.add_vrect(
            x0=r["run_date"], x1=r.get("_next_date", r["run_date"]),
            fillcolor=_REGIME_COLORS.get(r.get("tier", ""), "rgba(0,0,0,0)"),
            line_width=0, layer="below",
            label=dict(
                text=r.get("tier", "").replace("_", " ").title(),
                textposition="top left",
                font=dict(size=9, color="#64748b"),
            ),
        )
    fig.update_layout(**_base_layout("Equity Curve with Regime Overlay", 400,
        yaxis_title="Portfolio Value (₹)",
    ))
    fig.add_hline(y=1, line_dash="dot", line_color="#64748b", opacity=0.3)
    return fig
