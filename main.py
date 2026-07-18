"""
main.py — Main pipeline orchestrator (v2).

Entry point for the daily swing trade screener.
Always runs on every invocation — no trading day check.
Uses the latest available price data from the database.

Usage:
    python main.py              # normal run (writes to DB, sends Telegram)
    python main.py --dry-run    # full pipeline but no DB writes and no Telegram

Flow (v2):
    1.  Init DB
    2.  Create run record
    3.  Data ingestion (yfinance + Bhavcopy enrichment)
    4.  Align run_date to latest avail. price date
    5.  Indicator computation
    6.  RS Rating computation
    7.  Sector index data ingestion
    8.  Sector RS Rating computation
    9.  India VIX fetch
    10. Market breadth computation
    11. Stage 0: Tiered market regime (breadth + VIX)
    12. Load indicator rows
    13. Stage 1: Liquidity filter
    14. Stage 2: Trend template filter (regime-adjusted RS threshold)
    15. Stage 3: Pattern detection
    16. Sector trend cross-check
    17. Bearish override check (RS≥95 override candidates)
    18. Stage 4: Scoring & ranking (regime-adjusted max_picks)
    19. Trade parameter calculation (regime-adjusted sizing)
    20. Telegram delivery
    21. Mark run complete
"""

import sys
import argparse
import logging
from datetime import date

logger = logging.getLogger(__name__)

# ── Module imports ────────────────────────────────────────────────────────────
from db.database import init_db
from run_logging.run_logger import start_run, mark_complete, mark_failed
from data.ingestion import run_ingestion, fetch_index_data
from data.sector_data import fetch_sector_index_data, fetch_vix_value
from indicators.compute import compute_all_indicators
from indicators.rs_rating import compute_rs_ratings, compute_sector_rs_ratings
from indicators.breadth import calc_market_breadth
from screener.regime import check_market_regime, allow_bearish_override
from screener.filters import load_indicator_rows, run_liquidity_filter, run_trend_template_filter
from screener.patterns import run_pattern_filter
from screener.sector_check import run_sector_check
from screener.scoring import rank_and_select
from screener.trade_params import process_picks
from notifications.telegram import send_telegram_message
from notifications.formatter import (
    format_trade_message,
    format_error_message,
)

def load_latest_optimized_params() -> dict:
    """Fetch the most recently optimized parameters from walk-forward backtests."""
    from db.database import get_connection
    try:
        with get_connection() as conn:
            row = conn.execute(
                """SELECT rs_threshold, trend_conditions_required 
                   FROM backtest_walkforward_windows 
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
        if row and row["rs_threshold"] is not None:
            return {
                "rs_threshold": row["rs_threshold"],
                "trend_conditions_required": row["trend_conditions_required"]
            }
    except Exception:
        pass
    # Fallback to default configs
    from config import RS_THRESHOLD, TREND_CONDITIONS_REQUIRED
    return {
        "rs_threshold": RS_THRESHOLD,
        "trend_conditions_required": TREND_CONDITIONS_REQUIRED
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False) -> None:
    # Load and apply latest self-optimized parameters
    opt_params = load_latest_optimized_params()
    logger.info("Applying optimized strategy parameters: %s", opt_params)
    
    import config
    config.RS_THRESHOLD = opt_params["rs_threshold"]
    config.TREND_CONDITIONS_REQUIRED = opt_params["trend_conditions_required"]
    
    # Adjust regime tiers dynamically based on optimized base threshold
    t = opt_params["rs_threshold"]
    config.REGIME_TIERS["strong_bull"]["rs_threshold"] = t
    config.REGIME_TIERS["neutral_selective"]["rs_threshold"] = min(t + 10, 99)
    config.REGIME_TIERS["weak_selective"]["rs_threshold"] = min(t + 20, 99)
    config.REGIME_TIERS["bearish"]["rs_threshold"] = max(min(t + 25, 99), 95)

    run_date = date.today().isoformat()

    if dry_run:
        logger.info("=== DRY RUN MODE — no DB writes, no Telegram ===")

    init_db()
    run_id = None if dry_run else start_run()

    try:
        # Step 3: Data ingestion
        logger.info("── Step 3: Data ingestion ──")
        ingestion_stats = run_ingestion(run_id or "dry-run")
        logger.info("Ingestion stats: %s", ingestion_stats)

        # Step 4: Align run_date to latest available price date so
        # indicator rows JOIN correctly with daily_prices.
        last_available = _last_price_date()
        if last_available and last_available != run_date:
            logger.info("Using latest price date: %s", last_available)
            run_date = last_available

        # Fetch index data (used in-memory for RS, breadth, and regime)
        logger.info("── Fetching index data ──")
        index_df = fetch_index_data(run_id or "dry-run")

        # Step 5: Indicator computation
        logger.info("── Step 5: Indicator computation ──")
        indicator_stats = compute_all_indicators(run_date)
        logger.info("Indicators: %s", indicator_stats)

        # Step 6: RS Rating computation
        logger.info("── Step 6: RS Rating computation ──")
        rs_ratings = compute_rs_ratings(index_df, run_date)
        logger.info("RS ratings computed for %d symbols", len(rs_ratings))

        # Step 7: Sector index data ingestion
        logger.info("── Step 7: Sector index data ──")
        sector_dfs = fetch_sector_index_data(run_id or "dry-run")
        if sector_dfs:
            logger.info("Sector data fetched for %d sectors", len(sector_dfs))
        else:
            logger.warning("No sector data fetched — sector RS will default to 50.")

        # Step 8: Sector RS Rating computation
        logger.info("── Step 8: Sector RS Rating computation ──")
        sector_rs = compute_sector_rs_ratings(sector_dfs, index_df, run_date)
        logger.info("Sector RS ratings computed for %d symbols", len(sector_rs))

        # Step 9: Fetch India VIX (needed for breadth storage + regime)
        logger.info("── Step 9: India VIX ──")
        india_vix = fetch_vix_value()

        # Step 10: Market breadth computation
        logger.info("── Step 10: Market breadth ──")
        breadth = calc_market_breadth(run_date, index_df, india_vix=india_vix)

        # Step 11: Stage 0 — Tiered market regime (breadth + VIX)
        logger.info("── Step 11: Tiered market regime ──")
        regime_result = check_market_regime(breadth, india_vix, run_id or "dry-run", run_date)

        tier            = regime_result["tier"]
        max_picks       = regime_result["max_picks"]
        rs_threshold    = regime_result["rs_threshold"]
        risk_multiplier = regime_result["risk_multiplier"]

        # In bearish regime, don't stop — allow override candidates through
        bearish_with_overrides = False
        if tier == "bearish":
            logger.info("Bearish regime — will check for high-conviction override candidates.")
            bearish_with_overrides = True

        # Step 12: Load indicator rows for all symbols
        logger.info("── Step 12: Loading indicator rows ──")
        all_rows = load_indicator_rows(run_date)

        if not all_rows:
            logger.error(
                "No indicator rows found for %s — was indicator computation successful?", run_date
            )
            raise RuntimeError(f"No indicator rows available for {run_date}")

        # Step 13: Stage 1 — Liquidity filter
        logger.info("── Step 13: Liquidity filter ──")
        liquid = run_liquidity_filter(all_rows, run_id or "dry-run")

        # Step 14: Stage 2 — Trend template filter (regime-adjusted RS threshold)
        logger.info("── Step 14: Trend template filter (RS≥%d) ──", rs_threshold)
        trending = run_trend_template_filter(liquid, run_id or "dry-run", rs_threshold=rs_threshold)

        # Step 15: Stage 3 — Pattern detection
        logger.info("── Step 15: Pattern detection ──")
        setups = run_pattern_filter(trending, run_id or "dry-run")

        # Step 16: Sector trend cross-check (v2 spec §6.3)
        logger.info("── Step 16: Sector cross-check ──")
        sector_checked = run_sector_check(setups, run_id or "dry-run")

        # Step 17: Bearish override — if in bearish regime, check remaining
        # candidates for RS≥95 + near 52w high override candidates
        override_picks = []
        if bearish_with_overrides:
            logger.info("── Checking bearish override candidates ──")
            override_candidates = [r for r in sector_checked if allow_bearish_override(r)]
            if override_candidates:
                logger.info("Found %d override candidates", len(override_candidates))
                override_picks = rank_and_select(override_candidates, max_picks=1)
                for p in override_picks:
                    p["_is_override"] = True
            # Don't pass regular candidates through in bearish without override
            regular_picks = []
        else:
            regular_picks = sector_checked

        # Step 18: Stage 4 — Scoring & ranking (regime-adjusted max_picks)
        logger.info("── Step 18: Scoring & ranking (max=%d) ──", max_picks)
        top_picks = rank_and_select(regular_picks, max_picks=max_picks) if regular_picks else []
        # Merge override picks (they bypass scoring, ranked separately)
        all_picks = top_picks + override_picks
        # Limit to max_picks overall
        all_picks = all_picks[:max_picks]

        # Step 19: Trade parameters (regime-adjusted sizing)
        logger.info("── Step 19: Trade parameters ──")
        final_picks = process_picks(
            all_picks,
            run_id=run_id or "dry-run",
            run_date=run_date,
            market_regime=tier,
            risk_multiplier=risk_multiplier,
            regime_tier=tier,
        ) if not dry_run else _dry_run_trade_params(all_picks, tier, risk_multiplier)

        # Step 20: Telegram delivery
        logger.info("── Step 20: Telegram delivery ──")
        msg = format_trade_message(
            final_picks,
            run_date=run_date,
            regime=tier,
            regime_data=regime_result,
        )

        if dry_run:
            try:
                print("\n[DRY RUN] Telegram message:\n")
                print(msg)
            except UnicodeEncodeError:
                print("\n[DRY RUN] Telegram message (emoji stripped):\n")
                print(msg.encode("ascii", errors="replace").decode("ascii"))
            print(f"\n[DRY RUN] Pipeline stats:")
            print(f"  Ingested:         {ingestion_stats.get('stocks_ingested', 0)}")
            print(f"  Passed liquidity: {len(liquid)}")
            print(f"  Passed trend:     {len(trending)}")
            print(f"  Matched pattern:  {len(setups)}")
            print(f"  Passed sector:    {len(sector_checked)}")
            if bearish_with_overrides:
                print(f"  Override picks:   {len(override_picks)}")
            print(f"  Final picks:      {len(final_picks)}")
            print(f"  Regime tier:      {tier}")
            print(f"  RS threshold:     {rs_threshold}")
            print(f"  Risk multiplier:  {risk_multiplier}")
            return

        sent = send_telegram_message(msg, run_id=run_id)
        if sent:
            if final_picks:
                _mark_sent_to_telegram([p["screen_id"] for p in final_picks if "screen_id" in p])
        else:
            logger.warning("Telegram delivery failed — picks saved to DB but not sent.")

        # Step 21: Mark run complete
        mark_complete(run_id, {
            "stocks_ingested":          ingestion_stats.get("stocks_ingested", 0),
            "stocks_passed_liquidity":  len(liquid),
            "stocks_passed_trend":      len(trending),
            "stocks_passed_setup":      len(setups),
            "final_picks_count":        len(final_picks),
            "market_regime":            tier,
            "data_source_primary_pct":  ingestion_stats.get("data_source_primary_pct"),
        })

        logger.info(
            "Pipeline complete — %d picks sent to Telegram. run_id=%s, tier=%s",
            len(final_picks), run_id, tier,
        )

    except Exception as e:
        logger.exception("Pipeline failed with unhandled exception")
        if not dry_run and run_id:
            mark_failed(run_id, str(e))
            try:
                alert = format_error_message(run_date, str(e))
                send_telegram_message(alert, run_id=run_id)
            except Exception as tg_err:
                logger.error("Failed to send error alert to Telegram: %s", tg_err)
        raise
    finally:
        from db.database import flush_filter_logs
        flush_filter_logs(dry_run=dry_run)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dry_run_trade_params(top_picks: list[dict], market_regime: str,
                          risk_multiplier: float = 1.0) -> list[dict]:
    """
    Compute trade params in dry-run mode without writing to DB.
    Returns a simplified list for display purposes.
    """
    from screener.trade_params import calculate_trade_params, position_size
    from config import RISK_PER_TRADE_PCT

    effective_risk_pct = round(RISK_PER_TRADE_PCT * risk_multiplier, 2)

    picks = []
    for row in top_picks:
        setup_type  = row.get("_setup_type", "pullback")
        recent_high = row.get("_breakout_pivot")
        params = calculate_trade_params(row, setup_type, recent_high)
        if not params:
            continue
        shares = position_size(params["entry"], params["stop"], risk_pct=effective_risk_pct)
        picks.append({
            "symbol":             row.get("symbol"),
            "setup_type":         setup_type,
            "entry":              params["entry"],
            "stop":               params["stop"],
            "target":             params["target"],
            "risk_pct":           params["risk_pct"],
            "reward_risk_ratio":  params["reward_risk_ratio"],
            "rs_rating":          row.get("rs_rating", 0),
            "sector_rs_rating":   row.get("sector_rs_rating", 0),
            "shares":             shares,
            "effective_risk_pct": effective_risk_pct,
            "market_regime":      market_regime,
            "regime_tier":        market_regime,
            "is_override":        row.get("_is_override", False),
        })
    return picks


def _last_price_date() -> str | None:
    """Return the latest date available in daily_prices."""
    from db.database import get_connection
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(date) as md FROM daily_prices").fetchone()
    return row["md"] if row and row["md"] else None


def _mark_sent_to_telegram(screen_ids: list[str]) -> None:
    """Update sent_to_telegram flag in daily_screens."""
    from db.database import get_connection
    with get_connection() as conn:
        for sid in screen_ids:
            conn.execute(
                "UPDATE daily_screens SET sent_to_telegram=1 WHERE id=?",
                (sid,),
            )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nifty 100 Swing Trade Screener")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline without writing to DB or sending Telegram messages",
    )
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run)
