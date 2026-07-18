# Automated Nifty 100 Swing Trade Screener — Final System Specification (v2)

**Objective:** A fully automatic system that runs at 8:00 AM IST on NSE trading days, screens the Nifty 100 universe using a rule-based, auditable methodology, and sends 1–3 highest-conviction swing trade setups to Telegram with precise Entry, Stop Loss, and Take Profit levels for manual execution.

**Design philosophy:** No trained model. Deterministic and fully explainable at every stage. Uses breadth-based, tiered market regime detection (not a single index on/off switch) so genuine stock-level strength isn't blocked by index-level weakness — while still refusing to force trades when the broader market is genuinely unhealthy.

**Not financial advice.** This is a screening and risk-calculation tool. No system guarantees profitable trades — validate via backtest and shadow-mode tracking before risking capital.

---

## 1. High-Level Architecture

```
[GitHub Actions Cron — 8:00 AM IST, Mon–Fri]
        │
        ▼
[Run Logger: create run_id] ─────────────────────────► daily_run_log
        │
        ▼
[Trading Day Check] ── holiday/weekend ──► log + exit (no Telegram msg)
        │
        ▼
[Data Ingestion: NSE Bhavcopy → yfinance fallback] ──► daily_prices + ingestion_log
        │
        ▼
[Indicator Computation: MAs, ATR, RSI, RS Rating, Volume, Breadth] ──► daily_indicators
        │
        ▼
[Stage 0: Tiered Market Regime — Breadth + VIX + Sector] ──► regime_log
        │        (sets max_picks + RS threshold for the day, never a hard binary block)
        ▼
[Stage 1: Liquidity Filter] ──► filter_log
        │
        ▼
[Stage 2: Trend Template Filter] ──► filter_log
        │
        ▼
[Stage 3: Setup Pattern Detection] ──► filter_log
        │
        ▼
[Stage 4: Scoring & Ranking, regime-adjusted thresholds applied] 
        │
        ▼
[Entry / Stop / Target Calculator, regime-adjusted sizing] ──► daily_screens
        │
        ▼
[Telegram Delivery] ──► telegram_log
        │
        ▼
[Run Logger: mark complete/failed] ──► daily_run_log
```

**Important framing:** At 8 AM the market hasn't opened. Every "Entry" is a conditional trigger price based on the previous close, not a live fill. Every Telegram message makes this explicit.

---

## 2. Scheduling & Trading Day Logic

### 2.1 Cron Trigger (GitHub Actions)
```yaml
name: daily-swing-screener
on:
  schedule:
    - cron: '30 2 * * 1-5'   # 8:00 AM IST = 2:30 AM UTC, Mon–Fri
  workflow_dispatch: {}
jobs:
  run-screener:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 2.2 Holiday Calendar
```python
# Updated manually every December from NSE's official holiday circular
NSE_HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-06", "2026-03-21", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-08-27",
    "2026-10-02", "2026-10-21", "2026-11-09", "2026-12-25",
    # verify exact dates against the NSE circular before go-live
}

def is_trading_day(date) -> bool:
    if date.weekday() >= 5:
        return False
    if date.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        return False
    return True
```
Non-trading day → log skip reason to `daily_run_log`, exit silently. No Telegram message.

---

## 3. Universe: Nifty 100 Constituents

```sql
create table nifty100_constituents (
  symbol text primary key,
  company_name text,
  sector text,          -- maps to NSE sector index, e.g. 'NIFTY IT', 'NIFTY PHARMA'
  added_date date,
  removed_date date,
  is_active boolean default true
);
```
Updated manually, twice yearly (March/September rebalance) from NSE's official factsheet. The `sector` field is required — it drives the sector-rotation layer in Section 6.

---

## 4. Data Sourcing

### 4.1 Source Priority
1. **Primary — NSE Bhavcopy** (free, official EOD CSV, ~6–7 PM previous evening): OHLCV + delivery %.
2. **Fallback — yfinance**: used automatically if Bhavcopy fetch fails.
3. **Sector indices** — Nifty sector index EOD values (IT, Pharma, Auto, Bank, FMCG, Metal, Energy, etc.) — same sources.
4. **India VIX** — NSE daily EOD value, needed for regime tiering.

### 4.2 Ingestion Logic
```python
def fetch_eod_data(symbol, lookback_days=260):
    try:
        df = fetch_nse_bhavcopy(symbol, lookback_days)
        source = "nse_bhavcopy"
    except Exception as e:
        log_ingestion_event(symbol, status="fallback", error=str(e))
        df = fetch_yfinance(symbol, lookback_days)
        source = "yfinance"
    log_ingestion_event(symbol, status="success", source=source, rows=len(df))
    return df, source
```

### 4.3 Storage
```sql
create table daily_prices (
  symbol text, date date,
  open numeric, high numeric, low numeric, close numeric,
  volume bigint, delivery_pct numeric,
  source text,
  primary key (symbol, date)
);

create table sector_index_prices (
  sector text, date date,
  close numeric, sma50 numeric, sma200 numeric,
  primary key (sector, date)
);

create table market_breadth_daily (
  date date primary key,
  pct_above_sma50 numeric,
  pct_above_sma200 numeric,
  india_vix numeric,
  nifty100_close numeric,
  nifty100_sma50 numeric,
  nifty100_sma200 numeric
);
```
`daily_prices` grows permanently — this is your historical base for any future backtesting.

---

## 5. Indicator Computation

```python
import pandas_ta as ta

df['sma20']  = ta.sma(df['close'], length=20)
df['sma50']  = ta.sma(df['close'], length=50)
df['sma150'] = ta.sma(df['close'], length=150)
df['sma200'] = ta.sma(df['close'], length=200)
df['sma200_slope'] = df['sma200'].diff(21)
df['atr14']  = ta.atr(df['high'], df['low'], df['close'], length=14)
df['rsi14']  = ta.rsi(df['close'], length=14)
df['avg_vol_20'] = df['volume'].rolling(20).mean()
df['week52_high'] = df['close'].rolling(252).max()
df['week52_low']  = df['close'].rolling(252).min()
df['consolidation_tightness'] = df['close'].rolling(20).std() / df['close'].rolling(20).mean()
df['delivery_pct_slope'] = df['delivery_pct'].rolling(5).mean().diff(5)
```

### 5.1 RS Rating (vs Nifty 100 index, IBD-style weighting)
```python
def calc_rs_score(stock_df, index_df):
    def period_return(df, days):
        return (df['close'].iloc[-1] / df['close'].iloc[-days]) - 1
    r3m  = period_return(stock_df, 63)  - period_return(index_df, 63)
    r6m  = period_return(stock_df, 126) - period_return(index_df, 126)
    r12m = period_return(stock_df, 252) - period_return(index_df, 252)
    return (r3m * 0.4) + (r6m * 0.3) + (r12m * 0.3)

def assign_rs_ratings(raw_scores: dict):
    ranked = pd.Series(raw_scores).rank(pct=True) * 99
    return ranked.round().astype(int)
```

### 5.2 Sector RS Rating (same formula, sector index vs Nifty 100)
```python
def calc_sector_rs(sector_df, index_df):
    return calc_rs_score(sector_df, index_df)   # same weighting, applied to sector index
```

### 5.3 Market Breadth (replaces single index check)
```python
def calc_market_breadth(indicators_today: pd.DataFrame):
    pct_above_sma50 = (indicators_today['close'] > indicators_today['sma50']).mean() * 100
    pct_above_sma200 = (indicators_today['close'] > indicators_today['sma200']).mean() * 100
    return round(pct_above_sma50, 1), round(pct_above_sma200, 1)
```

### 5.4 Storage
```sql
create table daily_indicators (
  symbol text, date date,
  sma20 numeric, sma50 numeric, sma150 numeric, sma200 numeric, sma200_slope numeric,
  atr14 numeric, rsi14 numeric, avg_vol_20 numeric,
  week52_high numeric, week52_low numeric,
  consolidation_tightness numeric, delivery_pct_slope numeric,
  rs_rating int, sector_rs_rating int,
  primary key (symbol, date)
);
```

---

## 6. Stage 0 — Tiered Market Regime Filter (Breadth-Based)

This replaces a binary index gate, which would block genuinely strong stocks whenever the composite index dips — exactly the failure mode you flagged. Instead, regime sets **how selective** the system is, not whether it runs at all.

### 6.1 Regime Tiers
```python
def market_regime_tier(pct_above_sma200, india_vix, index_close, index_sma200):
    if pct_above_sma200 >= 60 and india_vix < 20:
        return {
            "tier": "strong_bull",
            "max_picks": 3,
            "rs_threshold": 70,
            "risk_multiplier": 1.0
        }
    elif pct_above_sma200 >= 40:
        return {
            "tier": "neutral_selective",
            "max_picks": 2,
            "rs_threshold": 80,
            "risk_multiplier": 0.85
        }
    elif pct_above_sma200 >= 25:
        return {
            "tier": "weak_selective",
            "max_picks": 1,
            "rs_threshold": 90,
            "risk_multiplier": 0.65
        }
    else:
        return {
            "tier": "bearish",
            "max_picks": 1,          # override-only, see 6.2
            "rs_threshold": 95,
            "risk_multiplier": 0.5
        }
```
`risk_multiplier` scales down position sizing (Section 8) in weaker regimes — you still trade, but smaller, which is the correct professional response to a deteriorating tape rather than a full stop.

### 6.2 Exceptional Override in Bearish Regime
Even when overall breadth is poor, a stock at RS ≥ 95 making new highs represents genuine leadership — historically these are often the names that lead the next up-move. Allowed through only in the `bearish` tier, explicitly flagged:
```python
def allow_bearish_override(row) -> bool:
    return row['rs_rating'] >= 95 and row['close'] >= row['week52_high'] * 0.98
```
These are tagged `⚡ high_conviction_override` in the output and sized at the reduced `risk_multiplier` for that tier — you're consciously trading against the broader tape, and the system makes that visible rather than hiding it.

### 6.3 Sector-Level Cross-Check
Before a candidate is finalized, check its **sector index** is not itself in a confirmed downtrend, independent of the Nifty 100 composite:
```python
def sector_trend_ok(sector_row) -> bool:
    return sector_row['close'] > sector_row['sma50']
```
This catches rotation directly — e.g. Pharma strength during an IT-led index drawdown is preserved; a stock whose own sector is broken is excluded even if the stock itself briefly passes other filters.

### 6.4 Logging
```sql
create table regime_log (
  run_id uuid,
  run_date date,
  pct_above_sma50 numeric,
  pct_above_sma200 numeric,
  india_vix numeric,
  nifty100_close numeric,
  nifty100_sma200 numeric,
  tier text,
  max_picks int,
  rs_threshold int,
  risk_multiplier numeric,
  override_candidates jsonb    -- symbols admitted via 6.2, if any
);
```

### 6.5 Validating This Design Choice
Before trusting this over the simple binary gate, backtest both versions on 3–5 years of Nifty 100 history and compare:
- Trades the binary gate would have blocked entirely during index softness that the tiered version caught and that won
- Bad trades let through by the looser tiers that the binary gate would have correctly avoided

This tells you honestly whether the added complexity is earning its keep, rather than assuming it automatically will.

---

## 7. Screening Funnel — Stages 1 to 4

### Stage 1 — Liquidity Filter
```python
def passes_liquidity(row) -> bool:
    turnover = row['close'] * row['avg_vol_20']
    return turnover > 5e7 and row['close'] > 30   # ₹5 Cr+ daily turnover, price > ₹30
```

### Stage 2 — Trend Template (Minervini), RS threshold from regime tier
```python
def passes_trend_template(row, rs_threshold) -> dict:
    conditions = {
        "above_sma150": row['close'] > row['sma150'],
        "above_sma200": row['close'] > row['sma200'],
        "sma150_above_sma200": row['sma150'] > row['sma200'],
        "sma50_above_sma150": row['sma50'] > row['sma150'],
        "above_sma50": row['close'] > row['sma50'],
        "above_25pct_off_low": row['close'] >= 1.25 * row['week52_low'],
        "within_25pct_of_high": row['close'] >= 0.75 * row['week52_high'],
        "sma200_trending_up": row['sma200_slope'] > 0,
        "rs_meets_regime_threshold": row['rs_rating'] >= rs_threshold,
    }
    passed_count = sum(conditions.values())
    return {"conditions": conditions, "passed": passed_count >= 7}
```

### Stage 3 — Setup Pattern Detection
```python
def detect_pullback_setup(row) -> bool:
    near_sma20 = abs(row['close'] - row['sma20']) / row['close'] < 0.02
    rsi_recovering = 40 <= row['rsi14'] <= 55
    return near_sma20 and rsi_recovering

def detect_breakout_setup(row, recent_high) -> bool:
    volume_confirms = row['volume'] > 1.5 * row['avg_vol_20']
    tight_base = row['consolidation_tightness'] < 0.05
    near_pivot = row['close'] >= recent_high * 0.98
    return volume_confirms and tight_base and near_pivot
```

Full pass/fail detail — including failures — logged per candidate:
```sql
create table filter_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid, symbol text,
  stage text,                -- 'liquidity' | 'trend_template' | 'setup_pattern' | 'sector_check'
  passed boolean,
  details jsonb,
  created_at timestamptz default now()
);
```

### Stage 4 — Scoring & Ranking
```python
def score_candidate(row) -> float:
    score = 0
    score += row['rs_rating'] * 0.28
    score += min(row['volume'] / row['avg_vol_20'], 3) * 20 * 0.20
    score += (1 - row['consolidation_tightness']) * 100 * 0.18
    score += (row['close'] / row['week52_high']) * 100 * 0.14
    score += row['sector_rs_rating'] * 0.12
    score += max(row['delivery_pct_slope'], 0) * 10 * 0.08
    return round(score, 2)
```
Rank by score, take up to `max_picks` from the regime tier. **Never pad the list** — if only 1 candidate qualifies on a `strong_bull` day allowing 3, send 1. If zero qualify, send zero with an explanation, not weaker forced picks.

---

## 8. Entry / Stop / Target Calculation (Regime-Adjusted)

```python
def calculate_trade_params(row, setup_type, risk_multiplier, recent_high=None) -> dict:
    atr = row['atr14']
    close = row['close']

    if setup_type == 'breakout':
        entry = recent_high * 1.002
        stop = entry - (1.5 * atr)
    else:  # pullback
        entry = close * 1.005
        stop = entry - (2 * atr)

    max_stop = entry * 0.92          # hard cap: never risk more than 8%
    stop = max(stop, max_stop)

    risk = entry - stop
    target = entry + (2.5 * risk)     # minimum 2.5:1 R:R

    nearest_resistance = row['week52_high']
    if entry < nearest_resistance < target:
        target = nearest_resistance

    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk_pct": round((risk / entry) * 100, 2),
        "reward_risk_ratio": round((target - entry) / risk, 2),
        "risk_multiplier_applied": risk_multiplier
    }
```

### Position Sizing — scaled by regime tier
```python
def position_size(capital, entry, stop, risk_multiplier, base_risk_pct=1.0):
    effective_risk_pct = base_risk_pct * risk_multiplier
    risk_amount = capital * (effective_risk_pct / 100)
    shares = int(risk_amount / (entry - stop))
    return shares, effective_risk_pct
```
In `strong_bull`, you risk the full 1% per trade. In `bearish` (override trades only), you risk 0.5% — same rule engine, automatically more conservative sizing when the backdrop is weaker, without ever going fully silent when leadership stocks exist.

---

## 9. Telegram Delivery

### 9.1 Setup
1. `@BotFather` → `/newbot` → bot token
2. Message the bot once → get chat ID via `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Store `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` as GitHub Actions secrets

### 9.2 Sending
```python
import requests

def send_telegram_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    resp = requests.post(url, json=payload, timeout=10)
    success = resp.status_code == 200
    log_telegram_event(payload=text, success=success, response=resp.text)
    return success
```

### 9.3 Message Format
```python
def format_trade_message(picks, date, regime):
    header = (
        f"📊 *Swing Screener — {date}*\n"
        f"Regime: {regime['tier']} "
        f"(Breadth: {regime['pct_above_sma200']}% above 200DMA, VIX: {regime['india_vix']})\n\n"
    )
    if not picks:
        return header + "No qualifying setups today — breadth and leadership both weak."

    msg = header
    for p in picks:
        flag = "⚡ High-conviction override\n" if p.get("override") else ""
        msg += (
            f"{flag}*{p['symbol']}* — {p['setup_type']}\n"
            f"Trigger Entry: ₹{p['entry']}\n"
            f"Stop Loss: ₹{p['stop']}  ({p['risk_pct']}% risk)\n"
            f"Target: ₹{p['target']}  (R:R {p['reward_risk_ratio']}:1)\n"
            f"RS Rating: {p['rs_rating']} | Sector RS: {p['sector_rs_rating']}\n"
            f"Suggested size: {p['shares']} shares ({p['effective_risk_pct']}% capital risk)\n\n"
        )
    msg += "⚠️ Conditional entries — confirm price action live before executing."
    return msg
```

---

## 10. Full Logging & Monitoring

### 10.1 Master Run Log
```sql
create table daily_run_log (
  run_id uuid default gen_random_uuid() primary key,
  run_date date,
  started_at timestamptz default now(),
  finished_at timestamptz,
  status text,                 -- 'success' | 'failed' | 'skipped'
  skip_reason text,
  error_message text,
  stocks_ingested int,
  stocks_passed_liquidity int,
  stocks_passed_trend int,
  stocks_passed_setup int,
  final_picks_count int,
  regime_tier text,
  data_source_primary_pct numeric
);
```

### 10.2 Ingestion Log
```sql
create table ingestion_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid, symbol text,
  status text, source text, rows_fetched int, error text,
  created_at timestamptz default now()
);
```

### 10.3 Regime Log — see Section 6.4

### 10.4 Filter Log — see Section 7

### 10.5 Final Screen Output
```sql
create table daily_screens (
  id uuid default gen_random_uuid() primary key,
  run_id uuid, screen_date date, symbol text,
  setup_type text, is_override boolean default false,
  entry numeric, stop numeric, target numeric,
  risk_pct numeric, reward_risk_ratio numeric,
  rs_rating int, sector_rs_rating int, score numeric,
  shares_suggested int, effective_risk_pct numeric,
  regime_tier text,
  sent_to_telegram boolean default false,
  created_at timestamptz default now()
);
```

### 10.6 Telegram Delivery Log
```sql
create table telegram_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid, message_text text,
  success boolean, api_response text,
  sent_at timestamptz default now()
);
```

### 10.7 Outcome Tracking (separate EOD job, e.g. 4 PM daily)
```sql
create table trade_outcomes (
  screen_id uuid references daily_screens(id),
  status text,                -- 'pending' | 'triggered' | 'stopped_out' | 'target_hit' | 'expired'
  triggered_at date, closed_at date,
  exit_price numeric, pnl_pct numeric, days_held int,
  updated_at timestamptz default now()
);
```
This is what eventually tells you the system's real hit rate, average realized R:R, and expectancy by regime tier — critically, you'll be able to see whether `weak_selective`/`bearish`-tier trades actually underperform `strong_bull`-tier trades as the risk_multiplier assumes, and tune the tiers with real evidence instead of guesswork.

### 10.8 Failure Alerting
```python
def main():
    run_id = start_run_log()
    try:
        if not is_trading_day(today):
            log_run_skip(run_id, reason="weekend_or_holiday")
            return
        # ... full pipeline ...
        complete_run_log(run_id, status="success")
    except Exception as e:
        log_run_failure(run_id, error=str(e))
        send_telegram_message(f"⚠️ Screener failed today ({today}). Check logs. Error: {str(e)[:200]}")
        raise
```
Guarantees silence only ever means "confirmed non-trading day" or "confirmed zero qualifying setups" — never an unexplained failure.

### 10.9 Weekly Health Check
Separate scheduled job (e.g. Sunday evening), summarizing the past 7 days from `daily_run_log` + `regime_log`:
```
📈 Weekly Health Check
Runs completed: 5/5
Regime tiers this week: strong_bull(2), neutral_selective(3)
Avg stocks passing trend template: 8.4
Avg data source (NSE primary): 96%
Picks sent: 7 (1 override)
Failures: 0
```

---

## 11. Build Order

1. Supabase schema — all tables including logging, from day one
2. Nifty 100 + sector mapping table (manual entry)
3. Data ingestion (stocks + sector indices + India VIX) → test standalone
4. Indicator computation, including breadth calculation
5. Stage 0 tiered regime logic + `regime_log` — validate breadth numbers by hand against a known day first
6. Stage 1–2 filters (liquidity + trend template, regime-adjusted RS threshold)
7. **Backtest binary-gate vs tiered-regime version on 3–5 years of data** (Section 6.5) before trusting the tiered design
8. Stage 3 pattern detection + sector cross-check
9. Scoring, ranking, override logic
10. Entry/Stop/Target + regime-adjusted position sizing
11. Telegram integration
12. Full run-log wrapping + failure alerting
13. GitHub Actions cron + holiday calendar
14. Second EOD job for `trade_outcomes`
15. **Shadow mode: 4–8 weeks minimum**, tracked but not traded, before risking capital
16. Weekly health check job
17. After 3+ months of outcome data: review whether tier thresholds (60/40/25 breadth cutoffs, RS thresholds, risk multipliers) match actual realized performance, and retune using real numbers

---

## 12. Environment / Secrets Checklist

| Secret | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | DB read/write |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Message delivery |
| (optional, future) `KITE_API_KEY` | Only if adding broker data/execution later |

All stored as GitHub Actions repository secrets — never hardcoded.

---

## 13. Key Design Decisions Summary

| Problem | Naive Approach | This Spec's Approach |
|---|---|---|
| Index dips but stocks are strong | Binary regime gate blocks all trades | Breadth-based tiers scale selectivity, don't fully block |
| Sector rotation masked by composite index | Not detected | Sector RS Rating + sector trend cross-check |
| Genuine leadership in a weak tape | Missed entirely | RS≥95 override, explicitly flagged, reduced size |
| Overconfident sizing in shaky markets | Fixed 1% risk regardless of regime | `risk_multiplier` scales size down as regime weakens |
| Silent failures mistaken for "no trades" | No distinction | Every run logged; failures trigger explicit Telegram alert |
| Untraceable "why wasn't X picked" | No record | Every candidate's pass/fail logged at every stage with full detail |
| No way to know if the system works | No tracking | `trade_outcomes` table tracks every pick to resolution |
