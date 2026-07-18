"""
backtest/engine.py — Point-in-time simulation engine.

Iterates day-by-day through historical data, runs the actual screener
functions against point-in-time data, simulates trades forward,
and records every signal and trade.
"""

import copy
import json
import logging
import uuid
from datetime import date, datetime, timedelta

import pandas as pd

from config import (
    LOOKBACK_DAYS, SECTOR_TICKERS, SECTOR_NAME_MAP,
    REGIME_TIERS, NIFTY100_TICKER, CATEGORY_WEIGHTS,
)
from db.database import get_connection
from data.ingestion import fetch_index_data
from data.sector_data import fetch_sector_index_data, fetch_vix_value
from indicators.compute import compute_indicators, load_price_history
from indicators.rs_rating import compute_rs_ratings, compute_sector_rs_ratings
from indicators.breadth import calc_market_breadth
from screener.regime import check_market_regime, allow_bearish_override
from screener.filters import load_indicator_rows, run_liquidity_filter, run_trend_template_filter
from screener.patterns import run_pattern_filter
from screener.sector_check import run_sector_check
from screener.scoring import rank_and_select
from screener.trade_params import calculate_trade_params, position_size

logger = logging.getLogger(__name__)

MAX_HOLDING_DAYS = 15      # max trading days to hold a position
TRIGGER_WINDOW = 5          # trading days to wait for entry trigger


class BacktestEngine:
    """Point-in-time backtesting engine for the Nifty 100 swing screener."""

    def __init__(self, start_date: str, end_date: str, run_name: str = "backtest",
                 risk_per_trade: float = 1.0, capital: float = 500_000,
                 disable_52w_cap: bool = False,
                 transaction_cost_pct: float = 0.25,
                 trend_conditions_required: int = None,
                 rs_threshold_override: int = None):
        self.start_date = start_date
        self.end_date = end_date
        self.run_name = run_name
        self.risk_per_trade = risk_per_trade
        self.capital = capital
        self.run_id = str(uuid.uuid4())
        self.disable_52w_cap = disable_52w_cap
        self.transaction_cost_pct = transaction_cost_pct
        self.trend_conditions_required = trend_conditions_required
        self.rs_threshold_override = rs_threshold_override

        # Collect all trading dates in range
        self._date_range: list[str] = []
        # Open positions being tracked
        self._open_positions: list[dict] = []
        # Completed trades
        self.trades: list[dict] = []
        # All signals generated (whether triggered or not)
        self.signals: list[dict] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """Run the full backtest simulation."""
        logger.info("Backtest starting: %s → %s (run_id=%s)", self.start_date, self.end_date, self.run_id)

        self._save_run_metadata()
        self._prepare_date_range()
        self._precompute_data()

        for i, d in enumerate(self._date_range):
            logger.debug("Simulating %s (%d/%d)", d, i + 1, len(self._date_range))

            # 1. Simulate existing open trades forward
            self._simulate_open_positions(d)

            # 2. Run screening pipeline for this date (generates new signals)
            self._run_screening_for_date(d)

        # Close any remaining open positions at end of backtest
        self._close_remaining_positions()

        # Compute and save metrics
        metrics = self._compute_and_save_metrics()

        logger.info(
            "Backtest complete: %d signals, %d triggered trades",
            len(self.signals), self._count_triggered(),
        )
        return metrics

    # ── Data preparation ─────────────────────────────────────────────────────

    def _prepare_date_range(self) -> None:
        """Build a list of trading dates in the backtest range by reading
        distinct dates from daily_prices."""
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT date FROM daily_prices
                   WHERE date >= ? AND date <= ?
                   ORDER BY date""",
                (self.start_date, self.end_date),
            ).fetchall()
        self._date_range = [r["date"] for r in rows]
        if not self._date_range:
            raise RuntimeError(
                f"No price data found from {self.start_date} to {self.end_date}. "
                "Run the pipeline first to populate daily_prices."
            )
        logger.info("Date range: %d trading days", len(self._date_range))

    def _precompute_data(self) -> None:
        """Ensure daily_indicators, sector_index_prices, market_breadth, and
        regime data exist for the full backtest date range.

        For each date, computes indicators using price data available as of
        that date (not forward-looking), so the simulation is point-in-time.
        """
        logger.info("Pre-computing indicators for all dates in range…")
        self._compute_indicators_for_all_dates()
        self._compute_breadth_and_regime_for_all_dates()
        logger.info("Pre-computation complete.")

    def _compute_indicators_for_all_dates(self) -> None:
        """Compute indicators for EVERY date in the range in bulk.

        For each symbol, computes rolling indicators for the full price
        history at once, then stores the row for each date in the range
        that falls within symbol's price coverage.
        """
        with get_connection() as conn:
            symbols = [
                r["symbol"]
                for r in conn.execute(
                    "SELECT symbol FROM nifty100_constituents WHERE is_active=1"
                ).fetchall()
            ]

        # Check actual price rows vs indicator rows in DB for this range to account for partial histories
        with get_connection() as conn:
            price_row = conn.execute(
                """SELECT COUNT(*) as c FROM daily_prices 
                   WHERE date >= ? AND date <= ? AND symbol IN (
                       SELECT symbol FROM nifty100_constituents WHERE is_active=1
                   )""",
                (self.start_date, self.end_date)
            ).fetchone()
            ind_row = conn.execute(
                """SELECT COUNT(*) as c FROM daily_indicators 
                   WHERE date >= ? AND date <= ?""",
                (self.start_date, self.end_date)
            ).fetchone()
        
        actual_price_rows = price_row["c"] if price_row else 0
        actual_ind_rows = ind_row["c"] if ind_row else 0
        
        if actual_ind_rows >= actual_price_rows * 0.8 and actual_price_rows > 0:
            logger.info("⏭️ Indicators already computed in database (%d/%d price rows). Skipping pre-computation.", actual_ind_rows, actual_price_rows)
            return

        computed = 0
        skipped = 0

        # Pre-cache price history and compute bulk indicators
        price_cache: dict[str, pd.DataFrame] = {}
        to_upsert = []
        for sym in symbols:
            df = load_price_history(sym, min_rows=50)
            if df is None:
                skipped += 1
                continue
            price_cache[sym] = df

            # Compute indicators for ALL rows at once
            bulk = _compute_indicators_bulk(df)
            if bulk is None or bulk.empty:
                continue

            # For each date in our range that has indicator data, accumulate
            date_set = set(self._date_range)
            for _, row in bulk.iterrows():
                d = row["date"]
                if d in date_set:
                    to_upsert.append((
                        sym, d,
                        row.get("sma20"),        row.get("sma50"),
                        row.get("sma150"),       row.get("sma200"),
                        row.get("sma200_slope"), row.get("atr14"),
                        row.get("rsi14"),        row.get("avg_vol_20"),
                        row.get("week52_high"),  row.get("week52_low"),
                        row.get("consolidation_tightness"),
                        row.get("delivery_pct_slope"),
                        row.get("rs_rating"),
                    ))
                    computed += 1

        # Bulk upsert all indicators in a single transaction
        if to_upsert:
            logger.info("Bulk upserting %d indicator rows to database...", len(to_upsert))
            with get_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO daily_indicators (
                        symbol, date, sma20, sma50, sma150, sma200, sma200_slope,
                        atr14, rsi14, avg_vol_20, week52_high, week52_low,
                        consolidation_tightness, delivery_pct_slope, rs_rating
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        sma20                   = excluded.sma20,
                        sma50                   = excluded.sma50,
                        sma150                  = excluded.sma150,
                        sma200                  = excluded.sma200,
                        sma200_slope            = excluded.sma200_slope,
                        atr14                   = excluded.atr14,
                        rsi14                   = excluded.rsi14,
                        avg_vol_20              = excluded.avg_vol_20,
                        week52_high             = excluded.week52_high,
                        week52_low              = excluded.week52_low,
                        consolidation_tightness = excluded.consolidation_tightness,
                        delivery_pct_slope      = excluded.delivery_pct_slope,
                        rs_rating               = COALESCE(excluded.rs_rating, daily_indicators.rs_rating)
                    """,
                    to_upsert
                )

        # Precompute Nifty 100 Index returns for all dates
        index_returns = {}
        index_df = self._load_point_in_time_index(self._date_range[-1])
        if index_df is not None and not index_df.empty:
            index_df = index_df.sort_values("date")
            close = index_df["close"]
            index_df["ret_252"] = close / close.shift(252) - 1
            index_df["ret_126"] = close / close.shift(126) - 1
            index_df["ret_63"]  = close / close.shift(63) - 1
            index_returns = index_df.set_index("date")[["ret_252", "ret_126", "ret_63"]].to_dict(orient="index")

        # Precompute Symbol returns for all dates
        symbol_returns = {}
        for sym, df in price_cache.items():
            df = df.sort_values("date")
            close = df["close"]
            df["ret_252"] = close / close.shift(252) - 1
            df["ret_126"] = close / close.shift(126) - 1
            df["ret_63"]  = close / close.shift(63) - 1
            symbol_returns[sym] = df.set_index("date")[["ret_252", "ret_126", "ret_63"]].to_dict(orient="index")

        # RS ratings computed separately per date
        rs_to_update = []
        for d in self._date_range:
            try:
                idx_ret = index_returns.get(d)
                if not idx_ret:
                    continue
                
                idx_63 = idx_ret.get("ret_63")
                idx_126 = idx_ret.get("ret_126")
                idx_252 = idx_ret.get("ret_252")
                if idx_63 is None or idx_126 is None or idx_252 is None:
                    continue
                if pd.isna(idx_63) or pd.isna(idx_126) or pd.isna(idx_252):
                    continue

                rel_returns = {}
                for sym in symbols:
                    sym_ret = symbol_returns.get(sym, {}).get(d)
                    if not sym_ret:
                        continue
                    s63 = sym_ret.get("ret_63")
                    s126 = sym_ret.get("ret_126")
                    s252 = sym_ret.get("ret_252")
                    if s63 is None or s126 is None or s252 is None:
                        continue
                    if pd.isna(s63) or pd.isna(s126) or pd.isna(s252):
                        continue
                    
                    rel = (s252 - idx_252) * 0.4 + (s126 - idx_126) * 0.3 + (s63 - idx_63) * 0.3
                    rel_returns[sym] = rel

                if rel_returns:
                    sorted_syms = sorted(rel_returns, key=rel_returns.get)
                    n = len(sorted_syms)
                    for rank, sym in enumerate(sorted_syms):
                        pct = int(round((rank + 1) / n * 99))
                        rating = min(max(pct, 1), 99)
                        rs_to_update.append((rating, sym, d))
            except Exception as e:
                logger.debug("RS rating failed for %s: %s", d, e)

        # Bulk update RS ratings in a single transaction
        if rs_to_update:
            logger.info("Bulk updating %d RS ratings in database...", len(rs_to_update))
            with get_connection() as conn:
                conn.executemany(
                    """UPDATE daily_indicators SET rs_rating = ?
                       WHERE symbol = ? AND date = ?""",
                    rs_to_update
                )

        logger.info("Indicators computed: %d rows, %d skipped", computed, skipped)

    def _load_point_in_time_index(self, d: str) -> pd.DataFrame | None:
        """Load index data up to date d (cached after first fetch)."""
        if not hasattr(self, "_index_cache"):
            try:
                from data.yfinance_fetcher import fetch_index_history
                self._index_cache = fetch_index_history(NIFTY100_TICKER, 500)
                logger.info("Index data cached: %d rows", len(self._index_cache) if self._index_cache is not None else 0)
            except Exception as e:
                logger.warning("Failed to fetch index data: %s", e)
                self._index_cache = pd.DataFrame()
        if self._index_cache is None or self._index_cache.empty:
            return None
        sub = self._index_cache[self._index_cache["date"] <= d].reset_index(drop=True)
        return sub if not sub.empty else None

    # ── Breadth & Regime ─────────────────────────────────────────────────────

    def _compute_breadth_and_regime_for_all_dates(self) -> None:
        """Compute market breadth for every date in the range and store
        in market_breadth_daily."""
        # Check if breadth already exists for this range
        with get_connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as c FROM market_breadth_daily 
                   WHERE date >= ? AND date <= ?""",
                (self.start_date, self.end_date)
            ).fetchone()
        
        expected_dates = len(self._date_range)
        if row and row["c"] >= expected_dates * 0.8 and expected_dates > 0:
            logger.info("⏭️ Breadth already computed in database (%d/%d rows). Skipping.", row["c"], expected_dates)
            return

        from data.yfinance_fetcher import fetch_index_history

        logger.info("Computing breadth and regime per date …")
        idx_df = fetch_index_history(NIFTY100_TICKER, 500)

        for d in self._date_range:
            try:
                index_subset = idx_df[idx_df["date"] <= d].reset_index(drop=True)
                if index_subset.empty:
                    continue
                breadth = self._calc_breadth_point_in_time(d)
                if breadth is None:
                    continue
                breadth["nifty100_close"] = float(index_subset["close"].iloc[-1])
                if len(index_subset) >= 50:
                    breadth["nifty100_sma50"] = float(index_subset["close"].rolling(50).mean().iloc[-1])
                if len(index_subset) >= 200:
                    breadth["nifty100_sma200"] = float(index_subset["close"].rolling(200).mean().iloc[-1])
                self._upsert_breadth(d, breadth)
            except Exception as e:
                logger.debug("Breadth failed for %s: %s", d, e)

    def _calc_breadth_point_in_time(self, d: str) -> dict | None:
        """Compute pct_above_sma50 and pct_above_sma200 for a single date."""
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT symbol, close FROM daily_prices
                   WHERE date = ?""",
                (d,),
            ).fetchall()
        if not rows:
            return None

        closes = {r["symbol"]: r["close"] for r in rows if r["close"]}
        if not closes:
            return None

        # Load all indicators for this date in a single query
        with get_connection() as conn:
            inds = conn.execute(
                """SELECT symbol, sma50, sma200 FROM daily_indicators
                   WHERE date = ?""",
                (d,),
            ).fetchall()
        
        ind_map = {r["symbol"]: (r["sma50"], r["sma200"]) for r in inds}

        above_50 = above_200 = total = 0
        for sym, close in closes.items():
            ind = ind_map.get(sym)
            if ind:
                sma50, sma200 = ind
                if sma50 and close > sma50:
                    above_50 += 1
                if sma200 and close > sma200:
                    above_200 += 1
                total += 1

        return {
            "pct_above_sma50":  round(above_50 / total * 100, 1) if total else 0,
            "pct_above_sma200": round(above_200 / total * 100, 1) if total else 0,
        }

    def _upsert_breadth(self, d: str, breadth: dict) -> None:
        with get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO market_breadth_daily
                   (date, pct_above_sma50, pct_above_sma200, india_vix,
                    nifty100_close, nifty100_sma50, nifty100_sma200)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (d, breadth.get("pct_above_sma50"), breadth.get("pct_above_sma200"),
                 breadth.get("india_vix"), breadth.get("nifty100_close"),
                 breadth.get("nifty100_sma50"), breadth.get("nifty100_sma200")),
            )

    def _upsert_indicator(self, symbol: str, d: str, ind: pd.Series) -> None:
        """Write a single indicator row to daily_indicators."""
        from indicators.compute import upsert_indicators
        try:
            upsert_indicators(symbol, d, ind)
        except Exception as e:
            logger.debug("Failed to upsert indicator %s/%s: %s", symbol, d, e)

    # ── Per-date screening ───────────────────────────────────────────────────

    def _run_screening_for_date(self, d: str) -> None:
        """Run the full screening pipeline for a single date using
        point-in-time data (date <= d)."""
        # Use the previous date's data for point-in-time screening
        # (we use d which is the date we're simulating — indicators exist for d,
        #  but in reality they'd be computed using data up to close of d-1)
        # Since daily_indicators for date d was computed using only data <= d-1
        # (because the indicator function uses the df subset up to d), and
        # daily_prices for date d includes data from the close of day d,
        # we use indicators at date d as the "as of close of d-1" state.
        # However, for signals, we need the indicator state BEFORE new prices
        # come in. The convention is:
        #   - Screening date = d
        #   - Indicators = indicator rows where date = last trading day before d
        # This ensures we never see today's price when deciding today's entry.
        prev_date = self._prev_trading_date(d)
        if prev_date is None:
            return

        # Load indicator rows joined with close prices from the PREVIOUS trading day
        with get_connection() as conn:
            prev_rows = conn.execute(
                """SELECT di.*, dp.close
                   FROM daily_indicators di
                   LEFT JOIN daily_prices dp ON di.symbol = dp.symbol AND di.date = dp.date
                   WHERE di.date = ?""",
                (prev_date,),
            ).fetchall()
        if not prev_rows:
            return

        # Cache sector map at engine level if not already cached
        if not hasattr(self, "_sector_cache"):
            with get_connection() as conn:
                sectors = conn.execute("SELECT symbol, sector FROM nifty100_constituents").fetchall()
            self._sector_cache = {s["symbol"]: s["sector"] for s in sectors}

        # Attach sector to each row
        all_rows = []
        for r in prev_rows:
            row = dict(r)
            row["sector"] = self._sector_cache.get(row["symbol"], "")
            all_rows.append(row)

        if not all_rows:
            return

        # Fetch index data for breadth/regime up to previous date
        index_df = self._load_point_in_time_index(prev_date)
        if index_df is None:
            return

        # Breadth for regime (use the pre-computed value from market_breadth_daily)
        with get_connection() as conn:
            b = conn.execute(
                "SELECT * FROM market_breadth_daily WHERE date = ?",
                (prev_date,),
            ).fetchone()
        breadth = dict(b) if b else {}

        # VIX — use stored value or default
        india_vix = breadth.get("india_vix", 15.0)

        # Regime tier
        from screener.regime import check_market_regime
        regime_result = check_market_regime(breadth, india_vix, run_id=self.run_id, run_date=d)
        tier = regime_result["tier"]
        max_picks = regime_result["max_picks"]
        rs_threshold = regime_result["rs_threshold"]
        risk_multiplier = regime_result["risk_multiplier"]

        if self.rs_threshold_override is not None:
            rs_threshold = self.rs_threshold_override

        # Fill RS ratings from the daily_indicators row
        rs_map = {r["symbol"]: (r["rs_rating"] or 50) for r in prev_rows}
        for row in all_rows:
            row["rs_rating_obj"] = rs_map.get(row["symbol"], 50)

        # Screening pipeline (identical to main.py steps)
        liquid = list(all_rows)  # Skip liquidity filter for backtest (use all symbols)
        # Instead, filter by price and volume manually
        liquid = [r for r in all_rows
                  if r.get("close") and r["close"] >= 30]

        trending = run_trend_template_filter(
            liquid, run_id=self.run_id, rs_threshold=rs_threshold,
            trend_conditions_required=self.trend_conditions_required,
            log_results=False
        )
        setups = run_pattern_filter(trending, run_id=self.run_id, log_results=False)
        sector_checked = run_sector_check(setups, run_id=self.run_id)

        # Bearish override
        override_picks = []
        if tier == "bearish":
            override_candidates = [r for r in sector_checked if allow_bearish_override(r)]
            if override_candidates:
                override_picks = rank_and_select(override_candidates, max_picks=1)
                for p in override_picks:
                    p["_is_override"] = True
            regular_picks = []
        else:
            regular_picks = sector_checked

        top_picks = rank_and_select(regular_picks, max_picks=max_picks) if regular_picks else []
        all_picks = (top_picks + override_picks)[:max_picks]

        for row in all_picks:
            self._generate_signal(row, d, tier, risk_multiplier, prev_date)

    def _generate_signal(self, row: dict, screen_date: str, regime_tier: str,
                         risk_multiplier: float, price_date: str) -> None:
        """Generate a trade signal for a single pick."""
        setup_type = row.get("_setup_type", "pullback")
        recent_high = row.get("_breakout_pivot")

        # Use the screening price as entry reference
        entry_candidate = self._get_price(row["symbol"], price_date)
        if entry_candidate is None:
            return

        trade_params = calculate_trade_params(
            row, setup_type, recent_high,
            disable_52w_cap=self.disable_52w_cap,
        )
        if not trade_params:
            return

        entry_price = trade_params["entry"]
        stop_price = trade_params["stop"]
        target_price = trade_params["target"]
        risk_pct = trade_params["risk_pct"]
        rr = trade_params["reward_risk_ratio"]

        effective_risk = round(self.risk_per_trade * risk_multiplier, 2)
        shares = position_size(entry_price, stop_price, risk_pct=effective_risk)
        if shares == 0:
            return

        position_entry_value = round(shares * entry_price, 2)

        signal = {
            "id": str(uuid.uuid4()),
            "symbol": row.get("symbol"),
            "signal_date": screen_date,
            "setup_type": setup_type,
            "regime_tier": regime_tier,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "risk_pct": risk_pct,
            "reward_risk_ratio": rr,
            "rs_rating": row.get("rs_rating_obj", 0),
            "score": row.get("score", 0),
            "is_override": row.get("_is_override", False),
        }

        self.signals.append(signal)
        self._open_positions.append({
            "signal": signal,
            "triggered": False,
            "trigger_date": None,
            "days_since_signal": 0,
            "exit_reason": None,
            "exit_price": None,
            "pnl_pct": None,
            "days_held": 0,
            "position_entry_value": position_entry_value,
            "high_since_entry": entry_price,
        })

    # ── Trade simulation ─────────────────────────────────────────────────────

    def _simulate_open_positions(self, current_date: str) -> None:
        """Simulate all open positions forward by one day."""
        remaining = []
        for pos in self._open_positions:
            sig = pos["signal"]
            symbol = sig["symbol"]

            high = self._get_price(symbol, current_date, "high")
            low = self._get_price(symbol, current_date, "low")
            close = self._get_price(symbol, current_date, "close")

            if high is None or low is None or close is None:
                remaining.append(pos)
                continue

            pos["days_held"] += 1

            if not pos["triggered"]:
                pos["days_since_signal"] += 1
                # Check if entry price was touched
                if low <= sig["entry_price"] <= high:
                    pos["triggered"] = True
                    pos["trigger_date"] = current_date
                    pos["days_held"] = 0
                elif pos["days_since_signal"] > TRIGGER_WINDOW:
                    # Expired — entry never triggered
                    self.trades.append({
                        "backtest_run_id": self.run_id,
                        "symbol": symbol,
                        "signal_date": sig["signal_date"],
                        "setup_type": sig["setup_type"],
                        "regime_tier": sig["regime_tier"],
                        "entry_price": sig["entry_price"],
                        "stop_price": sig["stop_price"],
                        "target_price": sig["target_price"],
                        "triggered": False,
                        "trigger_date": None,
                        "exit_date": current_date,
                        "exit_reason": "expired_not_triggered",
                        "exit_price": None,
                        "pnl_pct": None,
                        "days_held": None,
                        "rs_rating_at_entry": sig["rs_rating"],
                        "score_at_entry": sig["score"],
                    })
                    continue  # Don't add to remaining
                else:
                    remaining.append(pos)
                    continue

            # Position is triggered — check stop/target
            entry = sig["entry_price"]
            stop = sig["stop_price"]
            target = sig["target_price"]

            stop_hit = low <= stop
            target_hit = high >= target

            # Conservative: stop assumed hit first on same day
            if stop_hit and target_hit:
                exit_reason = "stopped_out"
                exit_price = stop
            elif stop_hit:
                exit_reason = "stopped_out"
                exit_price = stop
            elif target_hit:
                exit_reason = "target_hit"
                exit_price = target
            elif pos["days_held"] > MAX_HOLDING_DAYS:
                exit_reason = "timed_exit"
                exit_price = close
            else:
                remaining.append(pos)
                continue

            # Exit
            raw_pnl = ((exit_price - entry) / entry) * 100 if exit_reason != "expired_not_triggered" else 0
            pnl_pct = round(raw_pnl - self.transaction_cost_pct, 2)
            pnl_rupee = round((pnl_pct / 100) * pos["position_entry_value"], 2) if pnl_pct is not None else 0
            self.trades.append({
                "backtest_run_id": self.run_id,
                "symbol": symbol,
                "signal_date": sig["signal_date"],
                "setup_type": sig["setup_type"],
                "regime_tier": sig["regime_tier"],
                "entry_price": entry,
                "stop_price": stop,
                "target_price": target,
                "triggered": True,
                "trigger_date": pos["trigger_date"],
                "exit_date": current_date,
                "exit_reason": exit_reason,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_rupee": pnl_rupee,
                "position_entry_value": pos["position_entry_value"],
                "days_held": pos["days_held"],
                "rs_rating_at_entry": sig["rs_rating"],
                "score_at_entry": sig["score"],
            })

        self._open_positions = remaining

    def _close_remaining_positions(self) -> None:
        """Force-close positions still open at end of backtest."""
        for pos in self._open_positions:
            sig = pos["signal"]
            exit_reason = "timed_exit" if pos["triggered"] else "expired_not_triggered"
            exit_price = sig["entry_price"]  # flat at end
            pnl_pct = 0.0 if not pos["triggered"] else round(
                ((exit_price - sig["entry_price"]) / sig["entry_price"]) * 100, 2
            )
            pnl_rupee = round((pnl_pct / 100) * pos.get("position_entry_value", 0), 2)
            self.trades.append({
                "backtest_run_id": self.run_id,
                "symbol": sig["symbol"],
                "signal_date": sig["signal_date"],
                "setup_type": sig["setup_type"],
                "regime_tier": sig["regime_tier"],
                "entry_price": sig["entry_price"],
                "stop_price": sig["stop_price"],
                "target_price": sig["target_price"],
                "triggered": pos["triggered"],
                "trigger_date": pos.get("trigger_date"),
                "exit_date": self._date_range[-1] if self._date_range else self.end_date,
                "exit_reason": exit_reason,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "pnl_rupee": pnl_rupee,
                "position_entry_value": pos.get("position_entry_value", 0),
                "days_held": pos["days_held"],
                "rs_rating_at_entry": sig["rs_rating"],
                "score_at_entry": sig["score"],
            })
        self._open_positions = []

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _prev_trading_date(self, d: str) -> str | None:
        """Return the previous trading date from the date range."""
        idx = self._date_range.index(d) if d in self._date_range else -1
        if idx > 0:
            return self._date_range[idx - 1]
        return d  # Use current date if no previous

    def _get_price(self, symbol: str, date_str: str,
                   field: str = "close") -> float | None:
        """Get a single price field for a symbol+date (cached at date level)."""
        if not hasattr(self, "_price_cache_by_date"):
            self._price_cache_by_date = {}
            
        if date_str not in self._price_cache_by_date:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT symbol, open, high, low, close FROM daily_prices WHERE date=?",
                    (date_str,),
                ).fetchall()
            self._price_cache_by_date[date_str] = {
                r["symbol"]: {
                    "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"]
                }
                for r in rows
            }
            
        sym_map = self._price_cache_by_date[date_str]
        p_dict = sym_map.get(symbol)
        if p_dict:
            val = p_dict.get(field)
            return float(val) if val is not None else None
        return None

    def _load_price_history_up_to(self, symbol: str, max_date: str) -> pd.DataFrame | None:
        """Load price history for a symbol up to a specific date."""
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT date, open, high, low, close, volume, delivery_pct
                   FROM daily_prices
                   WHERE symbol = ? AND date <= ?
                   ORDER BY date ASC""",
                (symbol, max_date),
            ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame([dict(r) for r in rows])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")
        return df

    def _count_triggered(self) -> int:
        return sum(1 for t in self.trades if t.get("triggered"))

    # ── Metrics ──────────────────────────────────────────────────────────────

    def _compute_and_save_metrics(self) -> dict:
        """Compute all performance metrics and save to backtest_metrics table."""
        from backtest.metrics import compute_all_metrics
        metrics = compute_all_metrics(self.trades, self.signals, self.capital)
        self._save_metrics(metrics)
        return metrics

    def _save_run_metadata(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO backtest_runs
                   (id, run_name, start_date, end_date, parameters, regime_tiers, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (self.run_id, self.run_name, self.start_date, self.end_date,
                 json.dumps({
                     "risk_per_trade": self.risk_per_trade,
                     "capital": self.capital,
                     "max_holding_days": MAX_HOLDING_DAYS,
                     "trigger_window": TRIGGER_WINDOW,
                     "disable_52w_cap": self.disable_52w_cap,
                     "transaction_cost_pct": self.transaction_cost_pct,
                 }),
                 json.dumps(REGIME_TIERS, default=str)),
            )

    def _save_trades(self) -> None:
        with get_connection() as conn:
            for t in self.trades:
                conn.execute(
                    """INSERT OR IGNORE INTO backtest_trades
                       (id, backtest_run_id, symbol, signal_date, setup_type,
                        regime_tier, entry_price, stop_price, target_price,
                        triggered, trigger_date, exit_date, exit_reason,
                        exit_price, pnl_pct, days_held, rs_rating_at_entry,
                        score_at_entry, position_entry_value)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), t["backtest_run_id"], t["symbol"],
                     t["signal_date"], t["setup_type"], t["regime_tier"],
                     t["entry_price"], t["stop_price"], t["target_price"],
                     1 if t.get("triggered") else 0, t.get("trigger_date"),
                     t.get("exit_date"), t.get("exit_reason"),
                     t.get("exit_price"), t.get("pnl_pct"), t.get("days_held"),
                     t.get("rs_rating_at_entry"), t.get("score_at_entry"),
                     t.get("position_entry_value")),
                )

    def _save_metrics(self, metrics: dict) -> None:
        self._save_trades()
        with get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO backtest_metrics
                   (backtest_run_id, total_signals, total_triggered, win_rate,
                    win_rate_incl_timed_exits, avg_win_pct, avg_loss_pct,
                    avg_win_loss_ratio, profit_factor, expectancy_pct,
                    sharpe_ratio, sortino_ratio, max_drawdown_pct,
                    max_drawdown_duration_days, avg_days_held, total_return_pct,
                    cagr, benchmark_return_pct, benchmark_cagr, alpha,
                    calmar_ratio, metrics_by_regime_tier, metrics_by_setup_type,
                    num_walkforward_windows, walkforward_avg_win_rate,
                    walkforward_avg_expectancy, computed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (self.run_id,
                 metrics.get("total_signals"),
                 metrics.get("total_triggered"),
                 metrics.get("win_rate"),
                 metrics.get("win_rate_incl_timed_exits"),
                 metrics.get("avg_win_pct"),
                 metrics.get("avg_loss_pct"),
                 metrics.get("avg_win_loss_ratio"),
                 metrics.get("profit_factor"),
                 metrics.get("expectancy_pct"),
                 metrics.get("sharpe_ratio"),
                 metrics.get("sortino_ratio"),
                 metrics.get("max_drawdown_pct"),
                 metrics.get("max_drawdown_duration_days"),
                 metrics.get("avg_days_held"),
                 metrics.get("total_return_pct"),
                 metrics.get("cagr"),
                 metrics.get("benchmark_return_pct"),
                 metrics.get("benchmark_cagr"),
                 metrics.get("alpha"),
                 metrics.get("calmar_ratio"),
                 json.dumps(metrics.get("metrics_by_regime_tier", {})),
                 json.dumps(metrics.get("metrics_by_setup_type", {})),
                 metrics.get("num_walkforward_windows", 0),
                 metrics.get("walkforward_avg_win_rate"),
                 metrics.get("walkforward_avg_expectancy")),
            )


# ── Module-level helpers ──────────────────────────────────────────────────────


def _compute_indicators_bulk(df: pd.DataFrame) -> pd.DataFrame | None:
    """Compute indicators for EVERY row in a price history DataFrame.

    Uses the same logic as indicators/compute.py but returns all rows
    instead of only the last one.  Columns match the daily_indicators schema.

    Returns a DataFrame with columns: date, sma20, sma50, sma150, sma200,
    sma200_slope, atr14, rsi14, avg_vol_20, week52_high, week52_low,
    consolidation_tightness, delivery_pct_slope.
    Returns None if the DataFrame is too short.
    """
    if len(df) < 210:
        return None

    df = df.copy().sort_values("date").reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    import ta.trend as ta_trend
    import ta.volatility as ta_vol
    import ta.momentum as ta_mom
    import math

    result = pd.DataFrame({"date": df["date"]})

    result["sma20"] = ta_trend.sma_indicator(close, window=20)
    result["sma50"] = ta_trend.sma_indicator(close, window=50)
    result["sma150"] = ta_trend.sma_indicator(close, window=150)
    result["sma200"] = ta_trend.sma_indicator(close, window=200)
    result["sma200_slope"] = result["sma200"].diff(21)

    result["atr14"] = ta_vol.AverageTrueRange(
        high=high, low=low, close=close, window=14
    ).average_true_range()

    result["rsi14"] = ta_mom.RSIIndicator(close=close, window=14).rsi()

    result["avg_vol_20"] = volume.rolling(20).mean()

    result["week52_high"] = close.rolling(252, min_periods=200).max()
    result["week52_low"] = close.rolling(252, min_periods=200).min()

    result["consolidation_tightness"] = (
        close.rolling(20).std() / close.rolling(20).mean()
    )

    if "delivery_pct" in df.columns and df["delivery_pct"].notna().sum() > 10:
        result["delivery_pct_slope"] = (
            df["delivery_pct"].rolling(5).mean().diff(5)
        )
    else:
        result["delivery_pct_slope"] = 0.0

    # Replace NaN with None
    for col in result.columns:
        if col == "date":
            continue
        result[col] = result[col].apply(
            lambda v: None if (isinstance(v, float) and math.isnan(v)) else round(float(v), 6)
            if isinstance(v, (int, float)) else v
        )

    result["rs_rating"] = None
    return result
