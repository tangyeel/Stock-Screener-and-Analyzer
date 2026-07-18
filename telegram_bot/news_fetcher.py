"""
Fetch recent news for a company at query time.

Uses web search (via webfetch tool capabilities) to find headlines.
Categorized into: earnings, corporate_action, analyst_rating, sector, macro.

This is a lightweight integration that relies on a search API.
"""

import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

NEWS_SOURCES = [
    "moneycontrol.com", "economictimes.indiatimes.com",
    "business-standard.com", "livemint.com", "bloomberg.com",
    "reuters.com", "ndtv.com", "zeebiz.com",
]


def _categorize_headline(headline: str) -> str:
    hl = headline.lower()
    if any(w in hl for w in ("q1", "q2", "q3", "q4", "quarter", "revenue", "profit", "earnings", "results", "net profit", "net income")):
        return "earnings"
    if any(w in hl for w in ("bonus", "split", "dividend", "buyback", "rights issue", "stock split")):
        return "corporate_action"
    if any(w in hl for w in ("upgrade", "downgrade", "target price", "buy rating", "sell rating", "overweight", "underweight", "accumulate")):
        return "analyst_rating"
    if any(w in hl for w in ("sector", "industry", "regulatory", "rbi", "budget", "government", "policy")):
        return "sector"
    return "macro"


def fetch_news(query: str, ticker: str = "", days: int = 5) -> list[dict]:
    """Search for recent news about a company.
    
    Uses an available search mechanism. Returns categorized headlines.
    """
    cutoff = datetime.now() - timedelta(days=days)
    search_query = f"{query} {ticker}" if ticker else query

    try:
        # Try Google News RSS as a free source
        url = f"https://news.google.com/rss/search?q={search_query}&hl=en-IN&gl=IN&ceid=IN:en"
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
        content = resp.text
    except Exception as e:
        logger.warning("News fetch failed for %s: %s", search_query, e)
        return []

    items = []
    for line in content.split("<item>")[1:6]:
        try:
            title_start = line.find("<title>") + 7
            title_end = line.find("</title>")
            title = line[title_start:title_end].strip() if title_end > title_start else ""

            source_start = line.find("<source>")
            source = line[source_start + 8:line.find("</source>")].strip() if source_start > 0 else "News"

            link_start = line.find("<link>") + 6
            link_end = line.find("</link>")
            link = line[link_start:link_end].strip() if link_end > link_start else ""

            pub_start = line.find("<pubDate>") + 9
            pub_end = line.find("</pubDate>")
            pub_date_str = line[pub_start:pub_end].strip() if pub_end > pub_start else ""

            if not title:
                continue

            try:
                pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
            except (ValueError, TypeError):
                pub_date = datetime.now()

            if pub_date < cutoff:
                continue

            items.append({
                "headline": title,
                "source": source,
                "published_date": pub_date.strftime("%b %d"),
                "category": _categorize_headline(title),
                "url": link,
            })
        except Exception:
            continue

    return items[:5]
