"""
Fundamental heat-check via yfinance .info

Fetches key metrics and compares against sector medians (from Nifty 100 peers).
"""

import logging
from statistics import median

import yfinance as yf
import numpy as np

from db.database import get_connection

logger = logging.getLogger(__name__)


def _safe(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return val


def _get_sector_peers(sector: str) -> list[str]:
    if not sector:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT symbol FROM nifty100_constituents WHERE sector=? AND is_active=1",
            (sector,),
        ).fetchall()
    return [r["symbol"] for r in rows]


def _sector_median(peers: list[str], metric_key: str) -> float | None:
    """Fetch a metric for all peers and return the median value."""
    values = []
    for sym in peers:
        try:
            info = yf.Ticker(f"{sym}.NS").info
            val = _safe(info.get(metric_key))
            if val is not None and val > 0:
                values.append(val)
        except Exception:
            continue
    return median(values) if values else None


def check(ticker: str, sector: str | None) -> dict:
    # Indian NSE stocks need .NS suffix for yfinance fundamentals
    yf_ticker = f"{ticker}.NS" if not ticker.startswith("^") else ticker
    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        if not info or info.get("regularMarketPrice") is None:
            return {}
    except Exception as e:
        logger.debug("Fundamental fetch failed for %s: %s", ticker, e)
        return {}

    result = {}

    pe = _safe(info.get("trailingPE"))
    pb = _safe(info.get("priceToBook"))
    de = _safe(info.get("debtToEquity"))
    roe_pct = _safe(info.get("returnOnEquity"))
    div_yield = _safe(info.get("dividendYield"))
    sales_growth = _safe(info.get("revenueGrowth"))

    if pe is not None:
        result["pe"] = round(pe, 1)
        if pe < 15:
            result["pe_note"] = "Below 15 — potentially undervalued"
        elif pe < 25:
            result["pe_note"] = "Moderate"
        elif pe < 40:
            result["pe_note"] = "Above average — priced for growth"
        else:
            result["pe_note"] = "High — expect strong growth to justify"

    if pb is not None:
        result["pb"] = round(pb, 1)
        result["pb_note"] = "Above 3 — premium valuation" if pb > 3 else "Reasonable"

    if de is not None:
        result["de"] = round(de, 1)
        result["de_note"] = "Low debt" if de < 0.5 else "Moderate debt" if de < 1.5 else "High debt — watch interest cost"

    if roe_pct is not None:
        roe_pct = roe_pct * 100 if roe_pct < 1 else roe_pct  # yfinance sometimes returns already-pct
        result["roe"] = round(roe_pct, 1)
        result["roe_note"] = "Strong profitability" if roe_pct > 15 else "Adequate" if roe_pct > 10 else "Below average"

    if div_yield is not None:
        result["div_yield"] = round(div_yield * 100, 2) if div_yield < 1 else round(div_yield, 2)

    if sales_growth is not None:
        result["sales_growth"] = round(sales_growth * 100, 1)

    # Try sector comparison for P/E
    peer_symbols = _get_sector_peers(sector)
    if peer_symbols and pe is not None:
        sector_pe = _sector_median(peer_symbols, "trailingPE")
        if sector_pe:
            result["sector_pe"] = round(sector_pe, 1)
            result["pe_vs_sector"] = "above" if pe > sector_pe else "below"

    # Overall fundamental health score
    score, verdict = _calc_verdict(
        pe=pe, pb=pb, de=de, roe_pct=roe_pct,
        sales_growth=sales_growth,
        pe_vs_sector=result.get("pe_vs_sector"),
    )
    result["fundamental_score"] = score
    result["fundamental_verdict"] = verdict

    return result


def _calc_verdict(*, pe, pb, de, roe_pct, sales_growth, pe_vs_sector) -> tuple[int, str]:
    """
    Score a company's fundamentals on a -5 to +5 scale.
    Each metric contributes -1, 0, or +1.
    """
    s = 0
    if pe is not None:
        s += 1 if pe < 15 else 0 if pe < 30 else -1
    if pb is not None:
        s += 1 if pb < 3 else 0 if pb < 5 else -1
    if de is not None:
        s += 1 if de < 0.5 else 0 if de < 1.5 else -1
    if roe_pct is not None:
        s += 1 if roe_pct > 15 else 0 if roe_pct > 10 else -1
    if sales_growth is not None:
        s += 1 if sales_growth > 10 else 0 if sales_growth > 5 else -1
    if pe_vs_sector == "below":
        s += 1
    elif pe_vs_sector == "above":
        s -= 1

    if s >= 3:
        return s, "Strong"
    if s >= 1:
        return s, "Fair"
    if s >= 0:
        return s, "Mixed"
    if s >= -2:
        return s, "Weak"
    return s, "Poor"
