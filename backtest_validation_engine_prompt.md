# Build Prompt: Backtesting & Validation Engine for Nifty 100 Swing Screener

Copy everything below into Claude Code (or another coding assistant) to build this module. It assumes the screener's data pipeline (`daily_prices`, `daily_indicators`, `nifty100_constituents` tables in Supabase) already exists — this prompt is scoped to the backtest/validation layer only.

---

## PROMPT START

I have a rule-based swing trading screener for the Nifty 100 universe. I need you to build a complete backtesting and validation engine that tests the screener's logic against historical data, tracks every simulated trade to resolution, and computes industry-standard performance metrics. This needs to be rigorous enough that its output is trustworthy evidence of whether the strategy actually works — not just a demo.

### Context: What already exists
- A Supabase Postgres database with historical daily OHLCV data for Nifty 100 stocks in a `daily_prices` table (columns: symbol, date, open, high, low, close, volume, delivery_pct)
- A `daily_indicators` table with precomputed SMA20/50/150/200, ATR14, RSI14, avg_vol_20, week52_high/low, RS rating, sector RS rating
- A `nifty100_constituents` table with symbol, sector, added_date, removed_date, is_active
- Screening logic already defined in Python: a market regime tier function, a trend template filter (Minervini-style, requires 7+ of 9 conditions), a setup pattern detector (breakout/pullback), and a scoring/ranking function
- An entry/stop/target calculator using ATR-based stops (1.5x ATR for breakouts, 2x ATR for pullbacks, hard cap 8% max risk) and a minimum 2.5:1 reward:risk target

### What I need you to build

**1. Historical Data Requirements**
- Confirm/fetch at least 5 years of daily OHLCV history for all current and historical Nifty 100 constituents (handle index reconstitution — a stock that was in Nifty 100 in 2022 but isn't now should still be included for the period it was a constituent, to avoid survivorship bias)
- Write a data quality check that flags gaps, stale prices, or suspicious values (e.g., >20% single-day moves without a corresponding corporate action) before backtesting starts

**2. Point-in-Time Simulation Engine (critical — avoid lookahead bias)**
- For each historical trading day D, run the exact same screening pipeline (regime tier → liquidity filter → trend template → setup detection → scoring → entry/stop/target) using ONLY data available as of the close of day D-1 — never let the simulation see future data when generating a signal on day D
- This must replicate the real system exactly: same functions, same thresholds, same regime-tiering logic — the backtest engine should import and call the actual screening functions, not a reimplementation, so results reflect the real system
- For each signal generated, simulate the trade forward day-by-day starting from day D:
  - Trade "triggers" only if the entry price is actually touched within N days (define N, e.g. 5 trading days) — if not triggered, mark as "expired, not triggered" and exclude from win/loss stats (but log it separately)
  - Once triggered, walk forward day-by-day checking intraday high/low against stop and target
  - If both stop and target are touched on the same day, assume the STOP was hit first (conservative assumption, standard practice in backtesting to avoid overstating results)
  - If neither is hit within the max holding period (define, e.g. 15 trading days), close the trade at the close price on the final day and record it as a "timed exit"
  - Record: entry_date, trigger_date, exit_date, exit_reason (target_hit / stopped_out / timed_exit / expired_not_triggered), entry_price, exit_price, pnl_pct, days_held, regime_tier_at_entry, setup_type

**3. Database Schema for Backtest Results**
Create these tables in Supabase:
```sql
create table backtest_runs (
  id uuid default gen_random_uuid() primary key,
  run_name text,
  start_date date,
  end_date date,
  parameters jsonb,          -- full snapshot of thresholds/weights used, for reproducibility
  created_at timestamptz default now()
);

create table backtest_trades (
  id uuid default gen_random_uuid() primary key,
  backtest_run_id uuid references backtest_runs(id),
  symbol text,
  signal_date date,
  setup_type text,
  regime_tier text,
  entry_price numeric, stop_price numeric, target_price numeric,
  triggered boolean,
  trigger_date date,
  exit_date date,
  exit_reason text,          -- 'target_hit' | 'stopped_out' | 'timed_exit' | 'expired_not_triggered'
  exit_price numeric,
  pnl_pct numeric,
  days_held int,
  rs_rating_at_entry int,
  score_at_entry numeric
);

create table backtest_metrics (
  backtest_run_id uuid references backtest_runs(id) primary key,
  total_signals int,
  total_triggered int,
  win_rate numeric,               -- % of triggered trades that hit target vs stop (exclude timed exits or bucket separately, show both ways)
  win_rate_incl_timed_exits numeric,
  avg_win_pct numeric,
  avg_loss_pct numeric,
  avg_win_loss_ratio numeric,
  profit_factor numeric,           -- gross profit / gross loss
  expectancy_pct numeric,          -- (win_rate * avg_win) - (loss_rate * avg_loss)
  sharpe_ratio numeric,
  sortino_ratio numeric,
  max_drawdown_pct numeric,
  max_drawdown_duration_days int,
  avg_days_held numeric,
  total_return_pct numeric,        -- compounded, assuming fixed % risk per trade reinvested
  cagr numeric,
  benchmark_return_pct numeric,    -- Nifty 100 buy-and-hold over same period
  benchmark_cagr numeric,
  alpha numeric,                    -- strategy CAGR - benchmark CAGR
  calmar_ratio numeric,             -- CAGR / max drawdown
  metrics_by_regime_tier jsonb,     -- breakdown of win rate/expectancy per regime tier
  metrics_by_setup_type jsonb,      -- breakdown per breakout vs pullback
  computed_at timestamptz default now()
);
```

**4. Metrics Calculations — implement exactly these, with formulas**

- **Win rate**: triggered trades where exit_reason = 'target_hit' divided by total triggered trades (report both including and excluding timed_exit trades, since timed exits are ambiguous — could be small gains or small losses)
- **Profit factor**: sum of all winning trade P&L / abs(sum of all losing trade P&L). A value > 1.5 is generally considered healthy for swing strategies; > 2 is strong.
- **Expectancy**: `(win_rate × avg_win_pct) - (loss_rate × avg_loss_pct)` — the average expected return per trade. This is the single most important number for judging whether the strategy has edge, more so than win rate alone.
- **Sharpe Ratio**: compute using the trade-level or resampled-to-daily return series of the strategy (if capital is only in one trade at a time, construct a daily equity curve assuming the position sizing rules; if multiple trades can be open concurrently, construct a portfolio-level daily return series). Formula: `(mean_daily_return - daily_risk_free_rate) / std_dev_daily_return × sqrt(252)`. Use ~6-7% annualized as the risk-free rate proxy (Indian T-bill/repo rate) — make this a configurable parameter, not hardcoded, since it changes over time.
- **Sortino Ratio**: same as Sharpe but only penalizes downside deviation (std dev of negative returns only) — more appropriate than Sharpe for a strategy with asymmetric win/loss profiles like this one (2.5:1 min R:R means the return distribution is not symmetric).
- **Max Drawdown**: largest peak-to-trough decline in the cumulative equity curve, as a percentage. Also compute max drawdown duration (days from peak to full recovery).
- **CAGR**: compounded annual growth rate of the equity curve over the full backtest period.
- **Calmar Ratio**: CAGR / abs(max drawdown) — rewards strategies with good returns relative to their worst drawdown, a common way funds get compared.
- **Alpha vs benchmark**: compute Nifty 100 buy-and-hold CAGR over the identical backtest period, subtract from strategy CAGR. The strategy needs to prove it beats simply holding the index, risk-adjusted, or it's not worth the complexity of active trading.
- **Accuracy by regime tier**: break down every metric above separately for strong_bull / neutral_selective / weak_selective / bearish-override trades, so we can see whether the regime-tiering system (with its risk multipliers) is actually working as intended, or whether e.g. bearish-tier override trades are dragging down performance.
- **Accuracy by setup type**: same breakdown for breakout vs pullback setups, to see which setup type has the real edge.

**5. Walk-Forward Validation (mandatory, not optional)**
- Do NOT validate using a single backtest over the full 5-year period and calling it done — this overstates confidence
- Implement walk-forward validation: split the historical period into rolling windows (e.g., train/observe on a 12-month window, test the following 3 months out-of-sample, then roll forward by 3 months and repeat across the full dataset)
- Report metrics per out-of-sample window, not just in aggregate, so we can see consistency over time rather than one lucky/unlucky period dominating the result
- Flag any window where performance diverges sharply from the aggregate (potential regime-dependency or overfitting signal)

**6. Statistical Significance / Sanity Checks**
- Compute the number of independent trades — if total triggered trades across the full backtest is under ~30, explicitly flag in the output that sample size is too small for the metrics to be statistically meaningful, don't just report numbers as if they're reliable
- Run a simple randomization check: compare the strategy's results against N random entries (same position sizing/stop/target rules, but on randomly selected trend-template-qualifying days instead of the actual setup-triggered days) to sanity-check whether the setup detection logic (Stage 3) is adding real value beyond just "buying stocks in an uptrend" (Stage 2 alone)

**7. Reporting Output**
- Generate a markdown or HTML report per backtest run summarizing all metrics above, plus:
  - An equity curve chart (cumulative return over time) vs Nifty 100 buy-and-hold on the same chart
  - A drawdown chart
  - A table of the 10 best and 10 worst individual trades with symbol, dates, and P&L
  - The walk-forward window-by-window breakdown table
- Store this as a generated artifact per `backtest_run_id` so historical backtest runs can be compared against each other as the strategy's rules evolve over time

**8. Code Organization**
- Put this in a separate module (e.g. `/backtest/`) that imports the live screening functions rather than duplicating logic
- Include a CLI entrypoint: `python run_backtest.py --start 2020-01-01 --end 2025-12-31 --run-name "v2_tiered_regime"` so different rule versions can be backtested and compared by name
- Write unit tests for each metric calculation (Sharpe, Sortino, max drawdown, profit factor) against known/hand-calculated example datasets, so we trust the math before trusting the results

### What "done" looks like
Running the CLI on 5 years of Nifty 100 history produces: a populated `backtest_trades` table, a populated `backtest_metrics` row with every metric above computed correctly, a walk-forward breakdown showing consistency (or lack of it) across sub-periods, and a readable report comparing the strategy against simple Nifty 100 buy-and-hold — with an honest flag if sample size is too small to draw strong conclusions.

## PROMPT END
