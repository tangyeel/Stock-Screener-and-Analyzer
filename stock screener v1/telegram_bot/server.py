"""
FastAPI webhook server for the Telegram Stock Analysis Bot.

Run locally:
    uvicorn telegram_bot.server:app --reload --port 8000

Then in another terminal:
    ngrok http 8000
    curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<ngrok_id>.ngrok.io/webhook"

Usage:
    Text the bot any stock/company/index/fund name.
    /help for instructions.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import httpx
from fastapi import FastAPI, Request

from config import TELEGRAM_BOT_TOKEN, BOT_PORT
from db.database import init_db, get_connection
from telegram_bot.instrument_resolver import resolve, invalidate_cache
from telegram_bot.analysis_engine import run_analysis
from telegram_bot.fund_engine import analyze_fund
from telegram_bot.news_fetcher import fetch_news
from telegram_bot.response_formatter import (
    format_analysis, format_disambiguation, format_help, format_rate_limit,
    format_backtest_report,
)
from telegram_bot.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

app = FastAPI(title="Stock Analysis Bot")
rate_limiter = RateLimiter()

# Pending disambiguation: {chat_id: {"suggestions": [...], "created_at": datetime}}
_pending: dict[str, dict] = {}

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── Telegram API helpers ─────────────────────────────────────────────────────

async def send_message(chat_id: str, text: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "MarkdownV2",
            })
            if resp.status_code != 200:
                logger.warning("Telegram API error: %s %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False


# ── Logging helpers ───────────────────────────────────────────────────────────

def _log_resolution(chat_id: str, query: str, result: dict):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO resolution_log (id, chat_id, raw_query, resolved_ticker,
               resolved_type, confidence, method, score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), chat_id, query,
             result.get("match", {}).get("ticker") if result.get("match") else None,
             result.get("match", {}).get("instrument_type") if result.get("match") else None,
             result.get("confidence"), result.get("method"),
             result.get("score"), datetime.utcnow().isoformat()),
        )


def _log_query(chat_id: str, query: str, ticker: str, inst_type: str,
               analysis: dict, response_ms: int, news_count: int,
               status: str, error: str = None):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO query_log (id, chat_id, raw_query, resolved_ticker,
               instrument_type, composite_score, verdict, response_time_ms,
               news_items_count, status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), chat_id, query, ticker, inst_type,
             analysis.get("composite_score") if analysis else None,
             analysis.get("verdict") if analysis else None,
             response_ms, news_count, status, error, datetime.utcnow().isoformat()),
        )


def _to_json(obj):
    """Convert numpy types to native Python for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _to_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def _log_analysis(query_log_id: str, category: str, score: float, verdict: str, signals: dict):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO analysis_log (id, query_log_id, category, score, verdict, signals, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), query_log_id, category, score, verdict,
             json.dumps(_to_json(signals)), datetime.utcnow().isoformat()),
        )


# ── Processing ────────────────────────────────────────────────────────────────

async def process_query(chat_id: str, query: str):
    start = time.time()
    query_clean = query.strip()

    # Handle numeric reply for disambiguation
    if query_clean.isdigit() and chat_id in _pending:
        idx = int(query_clean) - 1
        pending = _pending[chat_id]
        if datetime.utcnow() - pending["created_at"] > timedelta(minutes=5):
            del _pending[chat_id]
            await send_message(chat_id, "That selection expired\\. Please send the name again\\.")
            return
        if 0 <= idx < len(pending["suggestions"]):
            suggestion = pending["suggestions"][idx]
            resolved = {"match": suggestion, "confidence": "disambiguation", "method": "user_selection"}
            del _pending[chat_id]
            _log_resolution(chat_id, query_clean, resolved)
            ticker = suggestion["ticker"]
            inst_type = suggestion.get("type", "equity")
            await _run_and_reply(chat_id, query_clean, resolved, ticker, inst_type, start)
            return
        else:
            await send_message(chat_id, f"Invalid selection\\. Please try again or send a different name\\.")
            return

    # Resolve instrument
    resolved = resolve(query_clean)
    _log_resolution(chat_id, query_clean, resolved)

    if resolved.get("error"):
        await send_message(chat_id, f"⚠️ {resolved['error']}")
        _log_query(chat_id, query_clean, None, None, None, 0, 0, "failed", resolved["error"])
        return

    if resolved["match"] is None:
        suggestions = resolved.get("suggestions", [])
        if suggestions:
            _pending[chat_id] = {"suggestions": suggestions, "created_at": datetime.utcnow()}
            msg = format_disambiguation(suggestions)
            await send_message(chat_id, msg)
            _log_query(chat_id, query_clean, None, None, None, 0, 0, "ambiguous")
        else:
            await send_message(chat_id, f"No match found for \"{query_clean}\"\\. Try /help for examples\\.")
            _log_query(chat_id, query_clean, None, None, None, 0, 0, "no_match")
        return

    match = resolved["match"]
    ticker = match["ticker"]
    inst_type = match["instrument_type"]
    await _run_and_reply(chat_id, query_clean, resolved, ticker, inst_type, start)


async def _run_and_reply(chat_id: str, raw_query: str, resolved: dict,
                         ticker: str, inst_type: str, start: float):
    try:
        if inst_type == "mutual_fund":
            analysis = analyze_fund(ticker)
            news = fetch_news(raw_query, ticker)
        else:
            sector = resolved["match"].get("sector")
            analysis = run_analysis(ticker, sector)
            news = fetch_news(resolved["match"]["primary_name"], ticker)

        elapsed = int((time.time() - start) * 1000)
        msg = format_analysis(resolved["match"], analysis, news)
        await send_message(chat_id, msg)

        verdict = analysis.get("verdict") if "verdict" in analysis else None
        score = analysis.get("composite_score") if "composite_score" in analysis else None
        error = analysis.get("error") if "error" in analysis else None
        status = "failed" if error else "success"

        _log_query(chat_id, raw_query, ticker, inst_type,
                   {"composite_score": score, "verdict": verdict},
                   elapsed, len(news), status, error)

        # Log individual category breakdowns
        categories = analysis.get("category_results", {})
        for cat_key, cat_result in categories.items():
            if isinstance(cat_result, dict):
                _log_analysis(
                    "", cat_key, cat_result.get("score", 0),
                    cat_result.get("verdict", "?"),
                    cat_result.get("signals", {}),
                )
    except Exception as e:
        logger.exception("Analysis failed for %s", raw_query)
        elapsed = int((time.time() - start) * 1000)
        await send_message(chat_id, f"⚠️ Analysis failed: {str(e)[:200]}")
        _log_query(chat_id, raw_query, ticker, inst_type, None, elapsed, 0, "failed", str(e))


# ── Queue drain (process messages sent while offline) ──────────────────────

async def drain_pending_queue():
    """Fetch and process any messages sent while the bot was offline.
    Deleted messages are automatically excluded by Telegram's API.
    Stores last processed update_id so they aren't re-fetched.
    """
    offset_file = Path("data/last_update.txt")
    last_id = 0
    if offset_file.exists():
        last_id = int(offset_file.read_text().strip())

    url = f"{TELEGRAM_API}/getUpdates"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            params = {"offset": last_id + 1, "timeout": 2, "allowed_updates": ["message"]}
            resp = await client.get(url, params=params)
            data = resp.json()

            if not data.get("ok"):
                logger.warning("getUpdates failed: %s", data.get("description", "unknown"))
                return

            updates = data.get("result", [])
            new_max = last_id
            count = 0
            for update in updates:
                uid = update.get("update_id", 0)
                msg = update.get("message", {})
                if msg and msg.get("text"):
                    chat_id = str(msg["chat"]["id"])
                    text = msg["text"].strip()
                    logger.info("Draining queued msg from %s: %s", chat_id, text[:60])
                    await process_query(chat_id, text)
                    count += 1
                new_max = max(new_max, uid)

            if updates:
                offset_file.parent.mkdir(parents=True, exist_ok=True)
                offset_file.write_text(str(new_max))
                logger.info("Drained %d queued message(s) (update_id → %d)", count, new_max)
            else:
                logger.info("No queued messages to drain.")
    except Exception as e:
        logger.warning("Drain failed: %s", e)


# ── Webhook endpoint ─────────────────────────────────────────────────────────

@app.post("/webhook")
@app.post("/telegram")
async def webhook(request: Request):
    body = await request.json()
    message = body.get("message", {})
    if not message or not message.get("text"):
        return {"ok": True}

    chat_id = str(message["chat"]["id"])
    text = message["text"].strip()

    # Rate limit check
    if not rate_limiter.is_allowed(chat_id):
        await send_message(chat_id, format_rate_limit())
        return {"ok": True}

    # Handle commands
    if text.startswith("/"):
        cmd = text.lower().split()[0]
        if cmd == "/start" or cmd == "/help":
            await send_message(chat_id, format_help())
            return {"ok": True}
        if cmd.startswith("/analyze"):
            query = text[len(cmd):].strip()
            if query:
                await send_message(chat_id, f"🔍 Analyzing \"{query}\"\\.\\.\\.")
                asyncio.create_task(process_query(chat_id, query))
            else:
                await send_message(chat_id, "Usage: /analyze <name>")
            return {"ok": True}
        if cmd == "/report":
            report = format_backtest_report()
            await send_message(chat_id, report)
            return {"ok": True}
        # Unknown command — treat as plain text
        pass

    # Plain text — analyze
    await send_message(chat_id, f"🔍 Analyzing \"{text}\"\\.\\.\\.")
    asyncio.create_task(process_query(chat_id, text))
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "bot_token_set": bool(TELEGRAM_BOT_TOKEN)}


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Bot server started on port %d", BOT_PORT)
    # Warm the instrument cache on first request
    invalidate_cache()


if __name__ == "__main__":
    import sys
    if "--drain" in sys.argv:
        asyncio.run(drain_pending_queue())
    else:
        import uvicorn
        uvicorn.run("telegram_bot.server:app", host="0.0.0.0", port=BOT_PORT, reload=True)
