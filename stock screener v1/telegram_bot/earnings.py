"""
Earnings calendar check — fetch upcoming earnings dates via yfinance.
Flags proximity to next earnings event.
"""

import logging
from datetime import datetime, date, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)


def check_earnings(ticker: str) -> dict | None:
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if cal is None or cal.empty:
            return None

        earnings_date = None
        for col in ["Earnings Date", "Earnings Date "]:
            if col in cal.index:
                val = cal.loc[col].iloc[0]
                if hasattr(val, "date"):
                    earnings_date = val.date()
                elif hasattr(val, "strftime"):
                    earnings_date = val.date()
                elif isinstance(val, str):
                    try:
                        earnings_date = datetime.strptime(val, "%Y-%m-%d").date()
                    except ValueError:
                        pass
                break

        if earnings_date is None:
            return None

        today = date.today()
        days_until = (earnings_date - today).days
        days_since = (today - earnings_date).days if today > earnings_date else None

        result = {
            "earnings_date": earnings_date.isoformat(),
            "days_until": days_until,
            "days_since": days_since,
        }

        if 0 <= days_until <= 14:
            if days_until == 0:
                result["flag"] = "TODAY"
                result["advice"] = "⚠️ Results today — avoid new positions before announcement"
            elif days_until <= 3:
                result["flag"] = "IMMINENT"
                result["advice"] = f"⚠️ Results in {days_until}d — gap risk, consider waiting"
            else:
                result["flag"] = "UPCOMING"
                result["advice"] = f"📅 Results in {days_until}d — position size accordingly"
        elif days_since is not None and 0 <= days_since <= 5:
            result["flag"] = "RECENT"
            result["advice"] = f"📊 Recent results ({days_since}d ago) \u2014 uncertainty cleared"
        else:
            result["flag"] = "CLEAR"
            result["advice"] = None

        return result
    except Exception as e:
        logger.debug("Earnings check failed for %s: %s", ticker, e)
        return None
