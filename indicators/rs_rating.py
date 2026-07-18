"""
indicators/rs_rating.py — IBD-style Relative Strength Rating.

Computes a percentile-ranked RS score (1–99) for every active symbol
relative to the Nifty 100 index, using weighted multi-period returns:
    40% × 3-month relative return
    30% × 6-month relative return
    30% × 12-month relative return

The most recent quarter is weighted heaviest, matching IBD's methodology.

After computing raw scores for all symbols, they're percentile-ranked so
the strongest stock always gets 99 and the weakest gets 1 — even in a
uniformly weak market. This is intentional: RS Rating is relative, not absolute.

Updates rs_rating in daily_indicators for the given date.
"""

import logging
from datetime import date

import pandas as pd

from db.database import get_connection

logger = logging.getLogger(__name__)


# ── Period lookup windows (trading days) ──────────────────────────────────────
PERIOD_3M  = 63
PERIOD_6M  = 126
PERIOD_12M = 252

# Weights for each lookback period (must sum to 1.0)
WEIGHTS = {
    "3m":  0.40,
    "6m":  0.30,
    "12m": 0.30,
}


def _period_return(series: pd.Series, days: int) -> float | None:
    """
    Compute the percentage return over the last `days` trading periods.
    Returns None if insufficient data.
    """
    if len(series) < days + 1:
        return None
    end   = series.iloc[-1]
    start = series.iloc[-days]
    if start == 0 or pd.isna(start) or pd.isna(end):
        return None
    return (end / start) - 1


def calc_raw_rs_score(
    stock_closes: pd.Series,
    index_closes: pd.Series,
) -> float | None:
    """
    Compute a single symbol's raw RS score vs the index.

    Args:
        stock_closes: Close price series for the stock (ascending date).
        index_closes: Close price series for Nifty 100 index (ascending date).

    Returns:
        Weighted relative return score, or None if not enough data.
    """
    score = 0.0
    total_weight = 0.0

    for label, days, weight in [
        ("3m",  PERIOD_3M,  WEIGHTS["3m"]),
        ("6m",  PERIOD_6M,  WEIGHTS["6m"]),
        ("12m", PERIOD_12M, WEIGHTS["12m"]),
    ]:
        stock_ret = _period_return(stock_closes, days)
        index_ret = _period_return(index_closes, days)

        if stock_ret is None or index_ret is None:
            # Skip this period; redistribute weight to others later
            continue

        relative = stock_ret - index_ret
        score += relative * weight
        total_weight += weight

    if total_weight == 0:
        return None

    # Rescale if some periods were skipped
    if total_weight < 1.0:
        score = score / total_weight

    return score


def assign_rs_ratings(raw_scores: dict[str, float]) -> dict[str, int]:
    """
    Percentile-rank raw RS scores to 1–99.

    Args:
        raw_scores: {symbol: raw_score}

    Returns:
        {symbol: rs_rating} where rs_rating is 1–99.
    """
    if not raw_scores:
        return {}

    series = pd.Series(raw_scores)
    # pct=True gives fractional rank 0–1; multiply by 99 and add 1 to get 1–99
    ranked = (series.rank(pct=True) * 99).clip(1, 99).round().astype(int)
    return ranked.to_dict()


def compute_rs_ratings(index_df: pd.DataFrame, run_date: str = None) -> dict[str, int]:
    """
    Compute RS Ratings for all active symbols and write to daily_indicators.

    Args:
        index_df:  Nifty 100 index price history DataFrame (columns: date, close).
        run_date:  ISO date string. Defaults to today.

    Returns:
        {symbol: rs_rating} dict.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    index_closes = pd.to_numeric(index_df["close"], errors="coerce").dropna()

    with get_connection() as conn:
        symbols = [
            r["symbol"]
            for r in conn.execute(
                "SELECT symbol FROM nifty100_constituents WHERE is_active=1"
            ).fetchall()
        ]

    raw_scores: dict[str, float] = {}

    # Batch-load all price data in one query instead of N+1
    with get_connection() as conn:
        all_rows = conn.execute(
            "SELECT symbol, close, date FROM daily_prices ORDER BY symbol, date"
        ).fetchall()

    if not all_rows:
        logger.warning("No price data found — cannot compute RS ratings.")
        return {}

    price_df = pd.DataFrame(all_rows, columns=["symbol", "close", "date"])
    price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")

    for symbol in symbols:
        stock = price_df[price_df["symbol"] == symbol]["close"]
        if len(stock) < 252:
            logger.debug("Insufficient history for %s — skipping RS.", symbol)
            continue
        score = calc_raw_rs_score(stock, index_closes)
        if score is not None:
            raw_scores[symbol] = score

    if not raw_scores:
        logger.warning("No RS scores computed — all symbols may have insufficient history.")
        return {}

    ratings = assign_rs_ratings(raw_scores)

    # Write back to daily_indicators in bulk
    insert_data = [
        (symbol, run_date, rating)
        for symbol, rating in ratings.items()
    ]
    if insert_data:
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO daily_indicators (symbol, date, rs_rating)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    rs_rating = excluded.rs_rating
                """,
                insert_data,
            )

    logger.info(
        "RS Ratings computed for %s: %d symbols. Top 5: %s",
        run_date,
        len(ratings),
        sorted(ratings.items(), key=lambda x: -x[1])[:5],
    )

    return ratings


def compute_sector_rs_ratings(
    sector_dfs: dict[str, pd.DataFrame],
    index_df: pd.DataFrame,
    run_date: str = None,
) -> dict[str, int]:
    """
    Compute sector-level RS ratings for every active symbol.

    For each symbol, looks up its sector and assigns the sector
    index's percentile-ranked RS score vs the Nifty 100 index.

    Args:
        sector_dfs: {sector_name: DataFrame with [date, close]} for each sector.
        index_df:   Nifty 100 index DataFrame with [date, close].
        run_date:   ISO date string. Defaults to today.

    Returns:
        {symbol: sector_rs_rating} dict (1-99).
    """
    if run_date is None:
        run_date = date.today().isoformat()

    index_closes = pd.to_numeric(index_df["close"], errors="coerce").dropna()

    sector_raw_scores: dict[str, float] = {}
    for sector_name, sector_df in sector_dfs.items():
        sector_closes = pd.to_numeric(sector_df["close"], errors="coerce").dropna()
        score = calc_raw_rs_score(sector_closes, index_closes)
        if score is not None:
            sector_raw_scores[sector_name] = score

    sector_ratings = assign_rs_ratings(sector_raw_scores)

    from config import SECTOR_NAME_MAP

    with get_connection() as conn:
        symbols = conn.execute(
            "SELECT symbol, sector FROM nifty100_constituents WHERE is_active=1"
        ).fetchall()

    symbol_sector_rs: dict[str, int] = {}
    for row in symbols:
        sym = row["symbol"]
        raw_sec = row["sector"]
        mapped_sec = SECTOR_NAME_MAP.get(raw_sec, raw_sec)
        if mapped_sec and mapped_sec in sector_ratings:
            symbol_sector_rs[sym] = sector_ratings[mapped_sec]

    insert_data = [
        (symbol, run_date, rating)
        for symbol, rating in symbol_sector_rs.items()
    ]
    if insert_data:
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO daily_indicators (symbol, date, sector_rs_rating)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    sector_rs_rating = excluded.sector_rs_rating
                """,
                insert_data,
            )

    logger.info(
        "Sector RS ratings computed for %d symbols across %d sectors.",
        len(symbol_sector_rs), len(sector_ratings),
    )

    return symbol_sector_rs
