# Automated Nifty 100 Swing Trade Screener — Full System Specification

**Objective:** A fully automatic system that runs at 8:00 AM IST on NSE trading days, screens the Nifty 100 universe using a rule-based (non-ML) methodology, and sends 2–3 highest-conviction swing trade setups to Telegram with precise Entry, Stop Loss, and Take Profit levels for manual execution.

**Scope note:** No trained model is used. This is a deterministic, auditable, industry-standard rule-based system (Minervini Trend Template + ATR risk framework + RS Rating ranking). Not financial advice — this is a screening tool, not a guarantee of profitable trades.

---

## 1. High-Level Architecture

```
[GitHub Actions Cron — 8:00 AM IST, Mon–Fri]
        │
        ▼
[Run Logger: create run_id, log start] ──────────────► daily_run_log
        │
        ▼
[Trading Day Check] ── if holiday/weekend ──► log skip reason ──► exit (no Telegram msg)
        │
        ▼
[Data Ingestion: NSE Bhavcopy → fallback yfinance] ──► daily_prices + ingestion_log
        │
        ▼
[Indicator Computation: MAs, ATR, RSI, RS Rating, Volume avg] ──► daily_indicators
        │
        ▼
[Stage 0: Market Regime Filter] ──► regime_log
        │
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
[Scoring & Ranking → Top 2–3] 
        │
        ▼
[Entry / Stop / Target Calculator] ──► daily_screens
        │
        ▼
[Telegram Delivery] ──► telegram_log
        │
        ▼
[Run Logger: mark run complete/failed] ──► daily_run_log
```

Every stage writes to a log table. This is deliberate — if the system ever produces a bad pick or goes silent, you should be able to reconstruct exactly what happened at every stage without re-running anything.

---

## 2. Scheduling & Trading Day Logic

### 2.1 Cron Trigger (GitHub Actions)
```yaml
name: daily-swing-screener
on:
  schedule:
    - cron: '30 2 * * 1-5'   # 8:00 AM IST = 2:30 AM UTC, Mon–Fri
  workflow_dispatch: {}       # allows manual trigger for testing
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

### 2.2 Holiday Calendar (in-script, secondary check)
```python
# Updated manually once a year from NSE's official holiday circular (published every December)
NSE_HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-06", "2026-03-21", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-08-27",
    "2026-10-02", "2026-10-21", "2026-11-09", "2026-12-25",
    # verify exact dates against NSE circular before go-live
}

def is_trading_day(date) -> bool:
    if date.weekday() >= 5:
        return False
    if date.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        return False
    return True
```
If not a trading day: log the skip with reason (`weekend` / `holiday`) to `daily_run_log`, exit silently — **no Telegram message sent** on non-trading days.

### 2.3 Important framing
At 8 AM the market has not opened. All output is based on the previous close. Every "Entry" is a **conditional trigger price** for today's session, not a live fill — the message language must always make this explicit so it's never misread as "already executed."

---

## 3. Universe: Nifty 100 Constituents

Stored in its own Supabase table since NSE rebalances Nifty 100 semi-annually (typically March and September).

```sql
create table nifty100_constituents (
  symbol text primary key,
  company_name text,
  sector text,
  added_date date,
  removed_date date,
  is_active boolean default true
);
```
Update process: manual, twice yearly, from NSE's official Nifty 100 factsheet PDF (nseindia.com). Not automated — the rebalance is infrequent and scraping NSE reliably is brittle enough that manual entry (10 minutes, twice a year) is the more robust choice.

---

## 4. Data Sourcing

### 4.1 Source Priority
1. **Primary — NSE Bhavcopy**: free official EOD CSV, published ~6–7 PM previous evening. Includes OHLCV + delivery %.
2. **Fallback — yfinance**: used automatically if Bhavcopy fetch fails (NSE occasionally rate-limits or the site is briefly down).
3. **Not used in this phase**: Kite Connect / broker APIs — unnecessary since execution is manual. Can be added later if you automate order placement.

### 4.2 Ingestion Logic
```python
def fetch_eod_data(symbol, lookback_days=260):
    # 260 days needed: 200 DMA requires ~200 trading days of history,
    # plus buffer for RS Rating lookback (12-month return)
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
  symbol text,
  date date,
  open numeric, high numeric, low numeric, close numeric,
  volume bigint,
  delivery_pct numeric,
  source text,               -- 'nse_bhavcopy' or 'yfinance'
  primary key (symbol, date)
);
```
This table grows daily and becomes your historical database for backtesting later — never delete from it.

---

## 5. Indicator Computation

Computed daily for every Nifty 100 constituent using `pandas-ta`.

```python
import pandas_ta as ta

df['sma20']  = ta.sma(df['close'], length=20)
df['sma50']  = ta.sma(df['close'], length=50)
df['sma150'] = ta.sma(df['close'], length=150)
df['sma200'] = ta.sma(df['close'], length=200)
df['sma200_slope'] = df['sma200'].diff(21)     # ~1 month trend direction
df['atr14']  = ta.atr(df['high'], df['low'], df['close'], length=14)
df['rsi14']  = ta.rsi(df['close'], length=14)
df['avg_vol_20'] = df['volume'].rolling(20).mean()
df['week52_high'] = df['close'].rolling(252).max()
df['week52_low']  = df['close'].rolling(252).min()
df['consolidation_tightness'] = (
    df['close'].rolling(20).std() / df['close'].rolling(20).mean()
)
df['delivery_pct_slope'] = df['delivery_pct'].rolling(5).mean().diff(5)
```

### 5.1 RS Rating (IBD-style, vs Nifty 100 index)
```python
def calc_rs_score(stock_df, index_df):
    def period_return(df, days):
        return (df['close'].iloc[-1] / df['close'].iloc[-days]) - 1

    r3m  = period_return(stock_df, 63)  - period_return(index_df, 63)
    r6m  = period_return(stock_df, 126) - period_return(index_df, 126)
    r12m = period_return(stock_df, 252) - period_return(index_df, 252)

    # Most recent quarter weighted heaviest — matches IBD's actual methodology
    return (r3m * 0.4) + (r6m * 0.3) + (r12m * 0.3)

# After computing raw scores for all 100 stocks, percentile-rank to 1-99
def assign_rs_ratings(raw_scores: dict):
    ranked = pd.Series(raw_scores).rank(pct=True) * 99
    return ranked.round().astype(int)
```

### 5.2 Storage
```sql
create table daily_indicators (
  symbol text,
  date date,
  sma20 numeric, sma50 numeric, sma150 numeric, sma200 numeric,
  sma200_slope numeric,
  atr14 numeric, rsi14 numeric,
  avg_vol_20 numeric,
  week52_high numeric, week52_low numeric,
  consolidation_tightness numeric,
  delivery_pct_slope numeric,
  rs_rating int,
  primary key (symbol, date)
);
```

---

## 6. Screening Funnel

### Stage 0 — Market Regime Filter (Nifty 100 index itself)
```python
def market_regime_check(index_row) -> dict:
    bullish = (
        index_row['close'] > index_row['sma50'] and
        index_row['close'] > index_row['sma200']
    )
    return {
        "regime": "bullish" if bullish else "bearish_or_choppy",
        "passed": bullish
    }
```
If regime fails → log to `regime_log`, send Telegram message stating no trades today due to market regime, **stop the pipeline** (Stages 1–4 not run). This is the single highest-value rule in the system — it prevents forcing picks in a downtrend.

### Stage 1 — Liquidity Filter
```python
def passes_liquidity(row) -> bool:
    turnover = row['close'] * row['avg_vol_20']
    return turnover > 5e7 and row['close'] > 30   # ₹5 Cr+ daily turnover, price > ₹30
```

### Stage 2 — Trend Template (Minervini)
```python
def passes_trend_template(row) -> dict:
    conditions = {
        "above_sma150": row['close'] > row['sma150'],
        "above_sma200": row['close'] > row['sma200'],
        "sma150_above_sma200": row['sma150'] > row['sma200'],
        "sma50_above_sma150": row['sma50'] > row['sma150'],
        "above_sma50": row['close'] > row['sma50'],
        "above_25pct_off_low": row['close'] >= 1.25 * row['week52_low'],
        "within_25pct_of_high": row['close'] >= 0.75 * row['week52_high'],
        "sma200_trending_up": row['sma200_slope'] > 0,
        "rs_top_30pct": row['rs_rating'] >= 70,
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
Every candidate that reaches this stage is logged to `filter_log` at every stage, including ones that failed — this lets you later answer "why didn't stock X show up today" without re-running anything.

```sql
create table filter_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid,
  symbol text,
  stage text,             -- 'liquidity' | 'trend_template' | 'setup_pattern'
  passed boolean,
  details jsonb,          -- full condition breakdown, e.g. trend template dict above
  created_at timestamptz default now()
);
```

### Stage 4 — Scoring & Ranking
```python
def score_candidate(row) -> float:
    score = 0
    score += row['rs_rating'] * 0.30
    score += min(row['volume'] / row['avg_vol_20'], 3) * 20 * 0.20  # capped ratio
    score += (1 - row['consolidation_tightness']) * 100 * 0.20
    score += (row['close'] / row['week52_high']) * 100 * 0.15
    score += row.get('sector_rs_rating', 50) * 0.10
    score += max(row['delivery_pct_slope'], 0) * 10 * 0.05
    return round(score, 2)
```
Take top 2–3 by score. If fewer than 2 candidates pass Stage 3, send whatever qualifies (including zero) rather than padding the list — never force a count.

---

## 7. Entry / Stop / Target Calculation

```python
def calculate_trade_params(row, setup_type, recent_high=None) -> dict:
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
        target = nearest_resistance   # don't project through obvious resistance

    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk_pct": round((risk / entry) * 100, 2),
        "reward_risk_ratio": round((target - entry) / risk, 2),
    }
```

### Position Sizing (included in output for completeness)
```python
def position_size(capital, entry, stop, risk_per_trade_pct=1.0):
    risk_amount = capital * (risk_per_trade_pct / 100)
    shares = int(risk_amount / (entry - stop))
    return shares
```

---

## 8. Telegram Delivery

### 8.1 One-time setup
1. Message `@BotFather` → `/newbot` → get bot token
2. Message your new bot once → get chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Store `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as GitHub Actions secrets

### 8.2 Sending
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

### 8.3 Message Format
```python
def format_trade_message(picks, date, regime):
    if not picks:
        return (
            f"📊 *Swing Screener — {date}*\n\n"
            f"Market regime: {regime}\n"
            f"No qualifying setups today.\n"
        )
    msg = f"📊 *Swing Screener — {date}*\nMarket regime: {regime}\n\n"
    for p in picks:
        msg += (
            f"*{p['symbol']}* — {p['setup_type']}\n"
            f"Trigger Entry: ₹{p['entry']}\n"
            f"Stop Loss: ₹{p['stop']}  ({p['risk_pct']}% risk)\n"
            f"Target: ₹{p['target']}  (R:R {p['reward_risk_ratio']}:1)\n"
            f"RS Rating: {p['rs_rating']}\n"
            f"Suggested size: {p['shares']} shares (1% capital risk)\n\n"
        )
    msg += "⚠️ Conditional entries — confirm price action live before executing."
    return msg
```

---

## 9. Full Logging & Monitoring System

This is the backbone that makes "purely automatic" trustworthy. Every run must be fully reconstructable after the fact.

### 9.1 Master Run Log
```sql
create table daily_run_log (
  run_id uuid default gen_random_uuid() primary key,
  run_date date,
  started_at timestamptz default now(),
  finished_at timestamptz,
  status text,                 -- 'success' | 'failed' | 'skipped'
  skip_reason text,             -- 'weekend' | 'holiday' | null
  error_message text,
  stocks_ingested int,
  stocks_passed_liquidity int,
  stocks_passed_trend int,
  stocks_passed_setup int,
  final_picks_count int,
  market_regime text,
  data_source_primary_pct numeric  -- % of stocks fetched via NSE vs yfinance fallback
);
```
Every run — including holidays/weekends where the script exits early, and full failures — creates exactly one row here. This single table answers "did the system run today, and what happened" at a glance.

### 9.2 Data Ingestion Log
```sql
create table ingestion_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid,
  symbol text,
  status text,               -- 'success' | 'fallback' | 'failed'
  source text,
  rows_fetched int,
  error text,
  created_at timestamptz default now()
);
```
Lets you catch data quality issues (e.g., a symbol silently returning fewer rows than expected) before they corrupt the screen.

### 9.3 Regime Log
```sql
create table regime_log (
  run_id uuid,
  run_date date,
  index_close numeric,
  index_sma50 numeric,
  index_sma200 numeric,
  regime text,
  passed boolean
);
```

### 9.4 Filter Log
(Defined in Section 6 above.) Captures every candidate's pass/fail at every stage with full condition detail in `jsonb`.

### 9.5 Final Screen Output Log
```sql
create table daily_screens (
  id uuid default gen_random_uuid() primary key,
  run_id uuid,
  screen_date date,
  symbol text,
  setup_type text,
  entry numeric, stop numeric, target numeric,
  risk_pct numeric, reward_risk_ratio numeric,
  rs_rating int,
  score numeric,
  shares_suggested int,
  market_regime text,
  sent_to_telegram boolean default false,
  created_at timestamptz default now()
);
```
This is your permanent trade-idea ledger — the basis for later performance tracking and any future backtesting/ML work.

### 9.6 Telegram Delivery Log
```sql
create table telegram_log (
  id uuid default gen_random_uuid() primary key,
  run_id uuid,
  message_text text,
  success boolean,
  api_response text,
  sent_at timestamptz default now()
);
```
If a message fails to send (network issue, bad token, Telegram API downtime), this is how you'd find out — combine with an alert (see 9.8).

### 9.7 Outcome Tracking (forward-fill, run separately from the 8 AM job)
A second, lightweight daily job (can run end-of-day, e.g. 4 PM) that checks open picks against live/EOD prices and updates outcomes:
```sql
create table trade_outcomes (
  screen_id uuid references daily_screens(id),
  status text,                -- 'pending' | 'triggered' | 'stopped_out' | 'target_hit' | 'expired'
  triggered_at date,
  closed_at date,
  exit_price numeric,
  pnl_pct numeric,
  days_held int,
  updated_at timestamptz default now()
);
```
This table is what eventually lets you compute your system's real hit rate, average R:R achieved, and expectancy — essential for knowing whether the system is actually working, and the exact data you'd need before ever considering the ML layer discussed earlier.

### 9.8 Failure Alerting
The main script must be wrapped end-to-end:
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
This guarantees that "no message today" always means either a confirmed non-trading day or a confirmed zero-setup day — never an unexplained silent failure.

### 9.9 Weekly Health Check
A separate scheduled job (e.g. Sunday evening) that queries `daily_run_log` for the past 7 days and sends a summary:
```
📈 Weekly Health Check
Runs completed: 5/5
Avg stocks passing trend template: 8.4
Avg data source (NSE primary): 96%
Picks sent this week: 7
Failures: 0
```

---

## 10. Build Order

1. Supabase schema — all tables above, including logging tables from the start (not bolted on later)
2. Nifty 100 constituent table (manual entry)
3. Data ingestion script → `daily_prices` + `ingestion_log`; test standalone
4. Indicator computation → `daily_indicators`
5. Stage 0–2 filters + `regime_log` / `filter_log`; manually sanity-check against stocks you recognize
6. **Backtest trend template + regime filter on 3–5 years of history** before adding pattern detection
7. Stage 3 pattern detection + scoring → `daily_screens`
8. Entry/Stop/Target calculator
9. Telegram integration + `telegram_log`
10. Full run-log wrapping + failure alerting
11. GitHub Actions cron wiring + holiday calendar
12. Second EOD job for `trade_outcomes` tracking
13. **Shadow mode: 4–8 weeks**, system sends picks, you track hypothetically before risking capital
14. Weekly health check job

---

## 11. Environment / Secrets Checklist

| Secret | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | DB read/write |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Message delivery |
| (optional) `KITE_API_KEY` | Only if adding broker data/execution later |

All stored as GitHub Actions repository secrets — never hardcoded in the script.
