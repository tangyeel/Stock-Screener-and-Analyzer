# Nifty 100 Swing Trade Screener

Automated daily screener for the Nifty 100 universe.
Runs at 8:00 AM IST on trading days, screens using Minervini Trend Template + ATR risk framework,
sends 2–3 highest-conviction setups to Telegram.

**Not financial advice. Conditional entries — confirm price action live before executing.**

---

## Setup

### 1. Install dependencies
```
pip install -r requirements.txt
```

### 2. Configure environment
```
copy .env.example .env
```
Edit `.env` and fill in:
- `TELEGRAM_BOT_TOKEN` — from @BotFather on Telegram
- `TELEGRAM_CHAT_ID` — your chat ID (message your bot, then check `https://api.telegram.org/bot<TOKEN>/getUpdates`)
- `CAPITAL` — your trading capital in INR (used for position sizing)

### 3. Initialise database and seed constituents
```
python scripts/seed_constituents.py
```
This creates `screener.db` and loads all 100 Nifty 100 symbols.

### 4. Test run (no DB writes, no Telegram)
```
python main.py --dry-run --force
```
`--force` bypasses the trading day check so you can test on weekends.

---

## Running

### Manual run
```
python main.py
```

### Dry run (test without side effects)
```
python main.py --dry-run --force
```

### Windows Task Scheduler (8:00 AM IST daily)
Create a task that runs:
```
python "G:\Stock Screener\main.py"
```
Working directory: `G:\Stock Screener`
Trigger: Daily at 8:00 AM

### Linux/Mac cron (8:00 AM IST = 2:30 AM UTC)
```
30 2 * * 1-5 cd /path/to/screener && /usr/bin/python3 main.py >> screener.log 2>&1
```

---

## Jobs

### EOD Outcome Tracker (run ~4 PM daily)
Checks open picks against day's price action and updates trade_outcomes:
```
python jobs/track_outcomes.py
```

### Weekly Health Check (run Sunday evening)
Sends a summary of the past week's runs to Telegram:
```
python jobs/weekly_health.py
```

---

## Project Structure

```
screener/
├── main.py                    # Pipeline entry point
├── config.py                  # All config + constants
├── requirements.txt
├── .env.example
├── db/
│   └── database.py            # SQLite connection + schema
├── data/
│   ├── nse_bhavcopy.py        # NSE EOD CSV fetcher
│   ├── yfinance_fetcher.py    # yfinance fallback
│   └── ingestion.py           # Orchestrates fetch + storage
├── indicators/
│   ├── compute.py             # All technical indicators
│   └── rs_rating.py           # IBD-style RS Rating
├── screener/
│   ├── regime.py              # Stage 0: Market regime
│   ├── filters.py             # Stage 1+2: Liquidity + Trend Template
│   ├── patterns.py            # Stage 3: Setup detection
│   ├── scoring.py             # Stage 4: Ranking
│   └── trade_params.py        # Entry/Stop/Target calculator
├── notifications/
│   ├── telegram.py            # Telegram API wrapper
│   └── formatter.py           # Message formatting
├── run_logging/
│   └── run_logger.py          # Run log management
├── jobs/
│   ├── track_outcomes.py      # EOD outcome tracker
│   └── weekly_health.py       # Weekly health check
└── scripts/
    ├── nifty100.csv            # Constituent list
    └── seed_constituents.py    # DB seeder
```

---

## Database (screener.db)

All data is local. Never commit `screener.db`.

Key tables:
- `daily_prices` — historical OHLCV (never delete rows — backtest source)
- `daily_indicators` — computed indicators per symbol per day
- `daily_screens` — final picks (permanent trade-idea ledger)
- `trade_outcomes` — how each pick played out
- `daily_run_log` — one row per pipeline run
- `filter_log` — every symbol's pass/fail at every stage (with full detail)
- `regime_log` — daily market regime check result

---

## Updating Nifty 100 Constituents

NSE rebalances Nifty 100 twice yearly (~March and September).
When that happens:
1. Update `scripts/nifty100.csv`
2. Run `python scripts/seed_constituents.py --replace`
