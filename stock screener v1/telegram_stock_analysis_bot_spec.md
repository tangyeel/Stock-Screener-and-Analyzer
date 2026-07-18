# On-Demand Telegram Stock Analysis Bot — Full Specification

**Objective:** Text a stock ticker, common company name, mutual fund name, or index name to a Telegram bot. The system automatically resolves it to the correct instrument, runs a deep multi-factor technical analysis using industry-standard methods, pulls the latest relevant news, and replies with a clear Bullish / Neutral / Bearish verdict plus the reasoning behind it.

**Relationship to the swing screener:** This is a separate, complementary system. The screener (previous spec) pushes 1-3 curated setups every morning on a schedule. This bot answers on-demand questions about *any* instrument, any time you send a message. They can share the same Supabase project and data pipeline.

**Not financial advice.** This is a technical-analysis summarization tool. It reports what the indicators say — it does not predict the future or guarantee outcomes.

---

## 1. High-Level Architecture

```
[User sends Telegram message: "TCS" / "hdfc bank" / "nifty bank" / "parag parikh flexicap"]
        │
        ▼
[Telegram Bot API → Webhook] ──────────────────────► POST to your endpoint
        │
        ▼
[Webhook Handler — Next.js API Route or Supabase Edge Function]
        │
        ├─► immediate ack: send "🔍 Analyzing..." to Telegram (keeps UX responsive)
        │
        ▼
[Instrument Resolver: text → ticker/scheme code + asset type] ──► resolution_log
        │
        ▼
[Asset-Type Router]
        │
        ├── EQUITY / INDEX ──► [Technical Analysis Engine] ──► analysis_log
        │
        └── MUTUAL FUND ──► [Fund Analysis Engine — different methodology] ──► analysis_log
        │
        ▼
[News Fetcher: latest relevant news, last 3-5 days] ──► news_log
        │
        ▼
[Verdict Synthesis: composite score across categories]
        │
        ▼
[Response Formatter] ──► Telegram sendMessage (full analysis)
        │
        ▼
[query_log: full record of query + response]
```

**Key architectural difference from the screener:** this needs a **webhook**, not a cron job — it must respond in real time when you send a message. GitHub Actions can't do this (it only runs on schedule/dispatch). Use a **Next.js API route deployed on Vercel** (fits your existing stack) or a **Supabase Edge Function** — either works; Next.js is the more natural fit given your current projects.

---

## 2. Telegram Webhook Setup

### 2.1 One-time configuration
```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://yourapp.vercel.app/api/telegram-webhook"
```
This tells Telegram to POST every incoming message to your endpoint instead of you having to poll.

### 2.2 Webhook Handler (Next.js API route)
```javascript
// /pages/api/telegram-webhook.js  (or app/api/telegram-webhook/route.js)
export default async function handler(req, res) {
  const { message } = req.body;
  if (!message?.text) return res.status(200).send("ok");

  const chatId = message.chat.id;
  const query = message.text.trim();

  // Respond to Telegram immediately (must ack within a few seconds)
  res.status(200).send("ok");

  // Send "analyzing" placeholder, then process async
  await sendTelegramMessage(chatId, `🔍 Analyzing "${query}"...`);
  await processAnalysisRequest(query, chatId);   // fire-and-forget, does the heavy work
}
```
Telegram only requires a fast 200 OK on the webhook call itself — the actual analysis + reply happens asynchronously via a separate `sendMessage` API call once processing completes. This avoids webhook timeouts on slower analysis runs.

### 2.3 Command Support (optional but useful)
Support a few explicit commands alongside free text:
- `/analyze <name>` — explicit trigger (same as just texting the name)
- `/help` — usage instructions
- Plain text with no command — treated as an analyze request by default (matches your requirement: "I should be able to text this name")

---

## 3. Instrument Resolution — Text → Ticker

This is the core new capability. It needs to handle four input types: exact tickers (`TCS`), common names (`tata consultancy`, `hdfc bank`), indices (`nifty bank`, `sensex`), and mutual funds (`parag parikh flexi cap`).

### 3.1 Reference Tables (built once, refreshed periodically)

```sql
create table instrument_master (
  id uuid default gen_random_uuid() primary key,
  instrument_type text,        -- 'equity' | 'index' | 'mutual_fund'
  ticker text,                 -- NSE symbol, index code, or AMFI scheme code
  primary_name text,           -- official name
  aliases text[],              -- common alternate names/abbreviations
  sector text,                 -- for equities
  exchange text,                -- 'NSE' | 'BSE' | 'INDEX' | 'MF'
  is_active boolean default true
);

create index idx_instrument_aliases on instrument_master using gin(aliases);
```

**Populating this table:**
- **Equities**: NSE's full equity list CSV (nseindia.com, "Equity List" download) — includes symbol + company name. ~2000 rows, refreshed monthly.
- **Indices**: manually curated list of ~20-30 major indices (Nifty 50, Nifty Bank, Nifty IT, Sensex, Nifty Midcap 100, etc.) with common aliases (`"bank nifty"` → `NIFTYBANK`, `"sensex"` → `BSESN`).
- **Mutual Funds**: AMFI's daily NAV file (free, `https://www.amfiindia.com/spages/NAVAll.txt`) — contains scheme code + full scheme name for every fund in India. Refreshed daily as part of the fund pipeline.

**Aliases** — auto-generate common variants when building the table:
```python
def generate_aliases(company_name: str, ticker: str) -> list[str]:
    aliases = [ticker.lower(), company_name.lower()]
    aliases.append(company_name.lower().replace("limited", "").replace("ltd", "").strip())
    words = company_name.replace("Limited", "").split()
    if len(words) > 1:
        aliases.append("".join(w[0] for w in words).lower())  # e.g. "TCS", "HDFC"
    return list(set(aliases))
```

### 3.2 Resolution Algorithm
```python
from rapidfuzz import process, fuzz

def resolve_instrument(query: str) -> dict:
    query_clean = query.strip().lower()

    # 1. Exact ticker match (fastest path)
    exact = supabase.table("instrument_master").select("*").ilike("ticker", query_clean).execute()
    if exact.data:
        return {"match": exact.data[0], "confidence": "exact", "method": "ticker"}

    # 2. Exact alias match
    alias_match = supabase.rpc("match_alias", {"query": query_clean}).execute()
    if alias_match.data:
        return {"match": alias_match.data[0], "confidence": "exact", "method": "alias"}

    # 3. Fuzzy match across primary_name + aliases (handles typos, partial names)
    all_instruments = get_cached_instrument_list()  # cached in memory/Redis, refreshed daily
    choices = {inst["id"]: f"{inst['primary_name']} {' '.join(inst['aliases'])}" for inst in all_instruments}
    best_match = process.extractOne(query_clean, choices, scorer=fuzz.WRatio)

    if best_match and best_match[1] >= 80:   # similarity threshold
        matched_inst = next(i for i in all_instruments if i["id"] == best_match[2])
        return {"match": matched_inst, "confidence": "fuzzy", "score": best_match[1], "method": "fuzzy"}

    # 4. No confident match — ask for clarification
    top3 = process.extract(query_clean, choices, scorer=fuzz.WRatio, limit=3)
    return {"match": None, "suggestions": top3}
```

### 3.3 Ambiguous Match Handling
If confidence is low or multiple plausible matches exist (e.g., "Tata" matches Tata Motors, Tata Steel, Tata Consumer, Tata Power), reply asking the user to pick:
```
Did you mean one of these?
1. TATAMOTORS — Tata Motors Ltd
2. TATASTEEL — Tata Steel Ltd
3. TATACONSUM — Tata Consumer Products Ltd

Reply with a number or the exact ticker.
```
Store the pending disambiguation keyed by `chat_id` for 5 minutes so a numeric reply (`2`) resolves correctly.

### 3.4 Logging
```sql
create table resolution_log (
  id uuid default gen_random_uuid() primary key,
  chat_id text, raw_query text,
  resolved_ticker text, resolved_type text,
  confidence text, method text, score numeric,
  created_at timestamptz default now()
);
```

---

## 4. Technical Analysis Engine (Equities & Indices)

This is the "industry standard, top-notch" deep-dive requested — modeled on how professional technical summary tools (e.g., the aggregation approach used by Investing.com's Technical Summary, TradingView's Technical Rating) combine trend, momentum, volume, and volatility into one composite read, rather than relying on a single indicator.

### 4.1 Data Required
```python
def fetch_analysis_data(ticker, lookback_days=300):
    daily = fetch_eod_data(ticker, lookback_days)       # daily OHLCV
    weekly = resample_to_weekly(daily)                   # higher-timeframe context
    sector_df = fetch_sector_index(get_sector(ticker))
    index_df = fetch_index_data("NIFTY500")               # broad benchmark for RS
    return daily, weekly, sector_df, index_df
```

### 4.2 Category 1 — Trend Analysis (Multi-Timeframe)
```python
def analyze_trend(daily_df, weekly_df):
    d = daily_df.iloc[-1]
    signals = {
        "price_above_sma20":  d['close'] > d['sma20'],
        "price_above_sma50":  d['close'] > d['sma50'],
        "price_above_sma150": d['close'] > d['sma150'],
        "price_above_sma200": d['close'] > d['sma200'],
        "ma_stack_bullish":   d['sma50'] > d['sma150'] > d['sma200'],
        "sma200_rising":      d['sma200_slope'] > 0,
        "weekly_trend_up":    weekly_df.iloc[-1]['close'] > weekly_df.iloc[-1]['sma30'],
        "adx_trending":       d['adx14'] > 25,   # ADX > 25 = trending market (Wilder's original threshold)
    }
    bullish_count = sum(signals.values())
    return {
        "signals": signals,
        "score": bullish_count / len(signals),   # 0.0 to 1.0
        "verdict": categorize_score(bullish_count / len(signals))
    }
```
**ADX (Average Directional Index)** is added here specifically — it's the industry-standard measure of trend *strength* (not direction), developed by Welles Wilder, widely used to distinguish "genuinely trending" from "choppy/directionless" price action.

### 4.3 Category 2 — Momentum
```python
def analyze_momentum(daily_df):
    d = daily_df.iloc[-1]
    macd_line, macd_signal, macd_hist = d['macd'], d['macd_signal'], d['macd_hist']

    signals = {
        "rsi_bullish_zone":     50 <= d['rsi14'] <= 70,       # strength without being extended
        "rsi_not_overbought":   d['rsi14'] < 80,
        "rsi_not_oversold":     d['rsi14'] > 20,
        "macd_above_signal":    macd_line > macd_signal,
        "macd_histogram_rising": d['macd_hist'] > daily_df.iloc[-2]['macd_hist'],
        "stoch_bullish":        d['stoch_k'] > d['stoch_d'],
        "roc_positive":         d['roc10'] > 0,               # 10-day rate of change
    }
    divergence = detect_rsi_divergence(daily_df)
    signals["no_bearish_divergence"] = not divergence["bearish"]

    bullish_count = sum(signals.values())
    return {
        "signals": signals,
        "divergence_flag": divergence,
        "score": bullish_count / len(signals),
        "verdict": categorize_score(bullish_count / len(signals))
    }
```
**MACD** (Moving Average Convergence Divergence, Appel) and **RSI divergence detection** are included because divergence — price and momentum disagreeing — is one of the most widely used professional warning signals for trend exhaustion, not just raw RSI level.

```python
def detect_rsi_divergence(df, lookback=20):
    recent = df.iloc[-lookback:]
    price_higher_high = recent['close'].iloc[-1] == recent['close'].max()
    rsi_lower_high = recent['rsi14'].iloc[-1] < recent['rsi14'].max()
    bearish_div = price_higher_high and rsi_lower_high

    price_lower_low = recent['close'].iloc[-1] == recent['close'].min()
    rsi_higher_low = recent['rsi14'].iloc[-1] > recent['rsi14'].min()
    bullish_div = price_lower_low and rsi_higher_low

    return {"bearish": bearish_div, "bullish": bullish_div}
```

### 4.4 Category 3 — Volume & Accumulation
```python
def analyze_volume(daily_df):
    d = daily_df.iloc[-1]
    obv_slope = calc_obv_slope(daily_df, window=20)

    signals = {
        "volume_above_avg":       d['volume'] > d['avg_vol_20'],
        "obv_rising":              obv_slope > 0,
        "delivery_pct_rising":     d['delivery_pct_slope'] > 0,   # India-specific: genuine buying vs churn
        "accumulation_pattern":    detect_accumulation(daily_df),  # up-days volume > down-days volume, 20d
    }
    bullish_count = sum(signals.values())
    return {
        "signals": signals,
        "score": bullish_count / len(signals),
        "verdict": categorize_score(bullish_count / len(signals))
    }

def calc_obv_slope(df, window=20):
    obv = ta.obv(df['close'], df['volume'])
    return (obv.iloc[-1] - obv.iloc[-window]) / window
```
**OBV (On-Balance Volume)**, developed by Joseph Granville, is a standard tool for detecting accumulation/distribution before it's obvious in price. **Delivery %** is India-market-specific and genuinely useful — high delivery percentage on up-moves indicates real investor buying rather than intraday speculation.

### 4.5 Category 4 — Volatility & Structure
```python
def analyze_volatility_structure(daily_df):
    d = daily_df.iloc[-1]
    bb_position = (d['close'] - d['bb_lower']) / (d['bb_upper'] - d['bb_lower'])

    signals = {
        "not_extended_from_bb":  0.2 <= bb_position <= 0.9,   # not slammed against either band
        "atr_pct_reasonable":     (d['atr14'] / d['close']) < 0.05,  # not excessively volatile
        "near_52w_high":          d['close'] >= 0.85 * d['week52_high'],
        "above_key_support":      d['close'] > find_nearest_support(daily_df),
    }
    bullish_count = sum(signals.values())
    return {
        "signals": signals,
        "bb_position": round(bb_position, 2),
        "score": bullish_count / len(signals),
        "verdict": categorize_score(bullish_count / len(signals))
    }
```
**Bollinger Bands** (John Bollinger) contextualize how "stretched" price is relative to its own recent volatility — a standard component of professional technical summaries.

### 4.6 Category 5 — Relative Strength
```python
def analyze_relative_strength(rs_rating, sector_rs_rating):
    signals = {
        "rs_above_median":         rs_rating >= 50,
        "rs_strong":                rs_rating >= 70,
        "sector_also_strong":       sector_rs_rating >= 50,
    }
    bullish_count = sum(signals.values())
    return {
        "rs_rating": rs_rating, "sector_rs_rating": sector_rs_rating,
        "signals": signals,
        "score": bullish_count / len(signals),
        "verdict": categorize_score(bullish_count / len(signals))
    }
```

### 4.7 Composite Verdict Synthesis
```python
CATEGORY_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.20,
    "volume": 0.20,
    "volatility_structure": 0.15,
    "relative_strength": 0.15,
}

def synthesize_verdict(category_results: dict) -> dict:
    composite_score = sum(
        category_results[cat]["score"] * weight
        for cat, weight in CATEGORY_WEIGHTS.items()
    )
    if composite_score >= 0.70:
        label = "Bullish"
    elif composite_score >= 0.45:
        label = "Neutral"
    else:
        label = "Bearish"

    return {
        "composite_score": round(composite_score, 2),
        "verdict": label,
        "category_breakdown": {
            cat: {"score": category_results[cat]["score"], "verdict": category_results[cat]["verdict"]}
            for cat in category_results
        }
    }

def categorize_score(score):
    if score >= 0.65: return "Bullish"
    if score >= 0.40: return "Neutral"
    return "Bearish"
```
This mirrors how professional aggregated technical-rating tools work: no single indicator decides the call — each category (trend/momentum/volume/volatility/RS) is scored independently, then combined with trend given the heaviest weight (consistent with the "trend is primary, everything else is confirmation" principle used across trend-following methodologies, including the Minervini approach used in the screener).

---

## 5. Fund Analysis Engine (Mutual Funds — Different Methodology)

Technical indicators built for price/volume don't transfer well to mutual fund NAVs (no volume, no intraday action, and momentum on a NAV series is mostly just tracking the underlying holdings). Industry-standard mutual fund analysis instead uses:

```python
def analyze_mutual_fund(scheme_code):
    nav_history = fetch_amfi_nav_history(scheme_code, days=1095)   # 3 years
    fund_meta = fetch_fund_metadata(scheme_code)   # category, expense ratio, AUM, benchmark

    rolling_returns = {
        "1y": calc_rolling_return(nav_history, 252),
        "3y_cagr": calc_cagr(nav_history, 756),
        "5y_cagr": calc_cagr(nav_history, 1260) if len(nav_history) >= 1260 else None,
    }
    volatility = calc_annualized_volatility(nav_history)
    sharpe = calc_sharpe_ratio(nav_history, risk_free_rate=0.07)   # approx Indian T-bill rate, update periodically
    max_drawdown = calc_max_drawdown(nav_history)

    category_avg = fetch_category_average_returns(fund_meta['category'])
    benchmark_return = fetch_benchmark_return(fund_meta['benchmark'], period="1y")

    return {
        "rolling_returns": rolling_returns,
        "volatility": volatility,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "vs_category_avg": rolling_returns["1y"] - category_avg,
        "vs_benchmark": rolling_returns["1y"] - benchmark_return,
        "expense_ratio": fund_meta['expense_ratio'],
        "aum_cr": fund_meta['aum'],
    }
```
Response framing for funds is explicitly different — "how has this fund performed vs its category and benchmark, risk-adjusted" — rather than a bullish/bearish technical call, since that framing doesn't meaningfully apply to a fund NAV.

---

## 6. News Integration

### 6.1 Sourcing
Fetched live at query time (not pre-cached, since relevance depends on the exact moment):
- **Web search** (query time): `"<company name>" news`, filtered to last 3-5 days
- Prioritize: company's own exchange filings/announcements (NSE/BSE corporate announcements), major financial press (Economic Times, Moneycontrol, Business Standard, LiveMint), analyst rating changes

```python
def fetch_recent_news(company_name, ticker, days=5):
    results = web_search(f"{company_name} {ticker} news")
    filtered = [r for r in results if is_within_days(r['date'], days)]
    categorized = categorize_news(filtered)   # earnings / corporate action / analyst rating / sector / macro
    return summarize_news_items(categorized)   # short, paraphrased, per copyright rules — no long quotes
```

### 6.2 What to Surface
- Earnings results or upcoming earnings date (directly affects near-term volatility — worth flagging even if just "results due in X days")
- Corporate actions (bonus, split, dividend, buyback)
- Analyst upgrades/downgrades and target price changes
- Sector-level news (regulatory changes, commodity price moves affecting the sector)
- Major macro events in the same window (RBI policy, budget, global market moves) if clearly relevant

### 6.3 Logging
```sql
create table news_log (
  id uuid default gen_random_uuid() primary key,
  chat_id text, ticker text,
  headline text, source text, published_date date,
  category text,          -- 'earnings' | 'corporate_action' | 'analyst_rating' | 'sector' | 'macro'
  url text,
  created_at timestamptz default now()
);
```

---

## 7. Response Format (Telegram Message)

```python
def format_analysis_response(instrument, verdict, categories, news_items, fund_data=None):
    if instrument['instrument_type'] == 'mutual_fund':
        return format_fund_response(instrument, fund_data, news_items)

    msg = (
        f"📈 *{instrument['primary_name']}* ({instrument['ticker']})\n"
        f"Overall: *{verdict['verdict']}* (score: {verdict['composite_score']}/1.0)\n\n"
        f"*Trend*: {categories['trend']['verdict']}\n"
        f"*Momentum*: {categories['momentum']['verdict']}"
        f"{' ⚠️ divergence flagged' if categories['momentum']['divergence_flag']['bearish'] else ''}\n"
        f"*Volume/Accumulation*: {categories['volume']['verdict']}\n"
        f"*Volatility/Structure*: {categories['volatility_structure']['verdict']}\n"
        f"*Relative Strength*: RS {categories['relative_strength']['rs_rating']} "
        f"({categories['relative_strength']['verdict']})\n\n"
    )

    if news_items:
        msg += "*Recent News:*\n"
        for n in news_items[:4]:
            msg += f"• {n['summary']} _({n['source']}, {n['published_date']})_\n"
        msg += "\n"

    msg += "⚠️ Technical analysis only — not a recommendation to buy or sell."
    return msg
```

Example output:
```
📈 Tata Consultancy Services (TCS)
Overall: Bullish (score: 0.74/1.0)

Trend: Bullish
Momentum: Neutral
Volume/Accumulation: Bullish
Volatility/Structure: Bullish
Relative Strength: RS 68 (Neutral)

Recent News:
• TCS reported Q1 results with revenue growth roughly in line with estimates (Moneycontrol, Jul 11)
• Brokerage raised target price citing steady deal pipeline (Economic Times, Jul 10)

⚠️ Technical analysis only — not a recommendation to buy or sell.
```

---

## 8. Full Logging

```sql
create table query_log (
  id uuid default gen_random_uuid() primary key,
  chat_id text,
  raw_query text,
  resolved_ticker text,
  instrument_type text,
  composite_score numeric,
  verdict text,
  response_time_ms int,
  news_items_count int,
  status text,             -- 'success' | 'no_match' | 'ambiguous' | 'failed'
  error text,
  created_at timestamptz default now()
);

create table analysis_log (
  id uuid default gen_random_uuid() primary key,
  query_log_id uuid references query_log(id),
  category text,            -- 'trend' | 'momentum' | 'volume' | 'volatility_structure' | 'relative_strength'
  score numeric, verdict text,
  signals jsonb,             -- full signal breakdown for auditability
  created_at timestamptz default now()
);
```
Every query is fully reconstructable — you can later check "what did it say about X on this date and why" without re-running anything, and this data is also what would eventually validate whether the category weights in Section 4.7 are well-calibrated.

---

## 9. Rate Limiting & Abuse Protection

Since this responds to any text message in real time:
```python
def check_rate_limit(chat_id) -> bool:
    recent_count = supabase.table("query_log") \
        .select("id", count="exact") \
        .eq("chat_id", chat_id) \
        .gte("created_at", (datetime.now() - timedelta(minutes=1)).isoformat()) \
        .execute()
    return recent_count.count < 10   # max 10 queries/minute per chat
```
Since this is presumably just for personal use (single chat ID), this is a light safety net rather than a critical control — but worth having so a rapid string of test messages doesn't hammer your data sources or API quotas.

---

## 10. Build Order

1. `instrument_master` table + population script (NSE equity list, index list, AMFI fund list)
2. Instrument resolver — test standalone against a variety of inputs (tickers, common names, typos, mutual funds, indices)
3. Telegram webhook endpoint (Next.js API route) + `setWebhook` configuration
4. Wire resolver into webhook, confirm round-trip: text in → resolved instrument confirmation out
5. Technical Analysis Engine — build category by category (trend → momentum → volume → volatility → RS), test each independently against known stocks
6. Composite verdict synthesis
7. Fund Analysis Engine (separate path)
8. News fetcher + summarizer
9. Response formatter
10. Full logging wiring
11. Rate limiting
12. Test extensively with real queries (tickers, misspelled names, ambiguous names, funds, indices) before relying on it

---

## 11. Environment / Secrets Checklist

| Secret | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | DB read/write |
| `TELEGRAM_BOT_TOKEN` | Bot API + webhook |
| `TELEGRAM_WEBHOOK_SECRET` | Verify incoming webhook requests are genuinely from Telegram |

---

## 12. Key Design Decisions Summary

| Requirement | How This Spec Addresses It |
|---|---|
| Text any name, not just exact ticker | `instrument_master` + fuzzy matching (rapidfuzz) with alias generation |
| Works for stocks, funds, and indices | Asset-type router — technical engine for equities/indices, separate fund-specific engine for mutual funds |
| "Industry standard" technical analysis | Multi-category aggregation (trend/momentum/volume/volatility/RS) mirroring how professional technical-rating tools synthesize signals, using well-established named indicators (ADX, MACD, RSI divergence, OBV, Bollinger Bands) rather than one arbitrary rule |
| Real-time via Telegram text | Webhook architecture (not cron) — Next.js API route responds to messages as they arrive |
| Latest news that could affect price | Live web search at query time, categorized (earnings/corporate action/analyst/sector/macro), paraphrased per copyright rules |
| Auditability | Every query, resolution, and category score logged for later review |
