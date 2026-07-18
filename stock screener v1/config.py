"""
config.py — Central configuration loader.
Reads from .env file and defines all system-wide constants.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Database ───────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "screener.db")

# ── Capital ────────────────────────────────────────────────────────────────────
CAPITAL: float = float(os.getenv("CAPITAL", "500000"))  # INR

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── NSE Holiday Calendar 2026 ──────────────────────────────────────────────────
# Source: NSE official holiday circular (published every December).
# Update this list manually at the start of each year.
NSE_HOLIDAYS: set[str] = {
    "2026-01-26",  # Republic Day
    "2026-03-06",  # Mahashivratri
    "2026-03-21",  # Holi
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-08-27",  # Ganesh Chaturthi
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-21",  # Dussehra
    "2026-11-09",  # Diwali (Laxmi Pujan)
    "2026-12-25",  # Christmas
}

# ── Screener Parameters ────────────────────────────────────────────────────────
LOOKBACK_DAYS: int = 260          # trading days of history needed
MIN_TURNOVER: float = 5e7         # ₹5 Cr daily turnover floor
MIN_PRICE: float = 30.0           # ₹30 minimum price
RS_THRESHOLD: int = 70            # RS Rating ≥ 70 (top 30%)
TREND_CONDITIONS_REQUIRED: int = 7  # out of 9 Minervini conditions
MAX_PICKS: int = 3                # max setups to send per day
MIN_PICKS: int = 0                # never force a minimum — send 0 if nothing qualifies

# ── Risk Parameters ────────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT: float = 1.0   # % of capital risked per trade
REWARD_RISK_RATIO: float = 2.5    # minimum R:R for target calculation
HARD_STOP_PCT: float = 0.08       # hard cap: never risk more than 8% from entry

# ── Nifty 100 Index Ticker ─────────────────────────────────────────────────────
NIFTY100_TICKER: str = "^CNX100"

# ── Sector Index Tickers (yfinance symbols for Nifty sector indices) ──────────
SECTOR_TICKERS: dict[str, str] = {
    "NIFTY IT":              "^CNXIT",
    "NIFTY PHARMA":          "^CNXPHARMA",
    "NIFTY AUTO":            "^CNXAUTO",
    "NIFTY BANK":            "^NSEBANK",
    "NIFTY FMCG":            "^CNXFMCG",
    "NIFTY METAL":           "^CNXMETAL",
    "NIFTY ENERGY":          "^CNXENERGY",
    # NIFTY CONSUMER DURABLES — not available via yfinance (ticker ^CNXCONDUR not found)
    # Stocks in this sector will pass through with sector_rs=50 and sector_trend_ok=True
}

# ── India VIX Ticker ───────────────────────────────────────────────────────────
INDIA_VIX_TICKER: str = "^INDIAVIX"

# ── Market Regime Tier Thresholds (breadth-based, v2 spec §6.1) ────────────────
REGIME_TIERS = {
    "strong_bull": {
        "pct_above_sma200_min": 60,
        "vix_max": 20,
        "max_picks": 3,
        "rs_threshold": 70,
        "risk_multiplier": 1.0,
        "tier": "strong_bull",
    },
    "neutral_selective": {
        "pct_above_sma200_min": 40,
        "vix_max": 999,
        "max_picks": 2,
        "rs_threshold": 80,
        "risk_multiplier": 0.85,
        "tier": "neutral_selective",
    },
    "weak_selective": {
        "pct_above_sma200_min": 25,
        "vix_max": 999,
        "max_picks": 1,
        "rs_threshold": 90,
        "risk_multiplier": 0.65,
        "tier": "weak_selective",
    },
    "bearish": {
        "pct_above_sma200_min": 0,
        "vix_max": 999,
        "max_picks": 1,
        "rs_threshold": 95,
        "risk_multiplier": 0.5,
        "tier": "bearish",
    },
}
# strong_bull also requires vix < 20; all other tiers ignore VIX for entry

# ── Telegram Bot ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
BOT_PORT: int = int(os.getenv("BOT_PORT", "8000"))

# ── Analysis Engine Weights ───────────────────────────────────────────────────
# Weights for the 5-category composite verdict (bot spec §4.7)
CATEGORY_WEIGHTS: dict[str, float] = {
    "trend": 0.30,
    "momentum": 0.20,
    "volume": 0.20,
    "volatility_structure": 0.15,
    "relative_strength": 0.15,
}

# ── Rate Limiting ──────────────────────────────────────────────────────────────
BOT_MAX_QUERIES_PER_MIN: int = 10

# ── Sector name mapping (DB constituent sector → Nifty sector index key) ─────
# The nifty100_constituents table uses broad industry names; this maps them
# to the Nifty sector index tickers used for sector RS and trend check.
SECTOR_NAME_MAP: dict[str, str] = {
    "Information Technology":                "NIFTY IT",
    "Healthcare":                            "NIFTY PHARMA",
    "Automobile and Auto Components":        "NIFTY AUTO",
    "Financial Services":                    "NIFTY BANK",
    "Fast Moving Consumer Goods":            "NIFTY FMCG",
    "Metals & Mining":                       "NIFTY METAL",
    "Oil Gas & Consumable Fuels":            "NIFTY ENERGY",
    "Consumer Durables":                     "NIFTY CONSUMER DURABLES",
}
