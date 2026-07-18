"""Utility functions for the dashboard."""

from datetime import datetime, timedelta


def fmt_pct(val: float | None, digits: int = 1) -> str:
    if val is None:
        return "—"
    return f"{val:+.{digits}f}%" if abs(val or 0) < 999 else f"{val:.{digits}f}%"


def fmt_num(val: float | None, suffix: str = "") -> str:
    if val is None:
        return "—"
    if abs(val) >= 1e7:
        return f"₹{val/1e7:.1f}Cr{suffix}"
    if abs(val) >= 1e5:
        return f"₹{val/1e5:.1f}L{suffix}"
    return f"{val:,.0f}{suffix}"


def fmt_int(val: int | None) -> str:
    if val is None:
        return "—"
    return f"{val:,}"


def safe_div(a: float | None, b: float | None, default: float | None = None) -> float | None:
    if a is None or b is None or b == 0:
        return default
    return a / b


def color_for_metric(val: float | None, higher_is_better: bool = True) -> str:
    if val is None:
        return "#64748b"
    if higher_is_better:
        if val > 0:
            return "#22c55e"
        if val < 0:
            return "#ef4444"
        return "#eab308"
    else:
        if val < 0:
            return "#22c55e"
        if val > 0:
            return "#ef4444"
        return "#eab308"
