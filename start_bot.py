#!/usr/bin/env python3
"""
start_bot.py — Single command: fetch prices → backtest → start bot + ngrok tunnel.

Usage:
    python start_bot.py
    python start_bot.py --force          # re-run backtest even if done today
    python start_bot.py --no-ngrok       # skip ngrok tunnel (use existing webhook or polling)

What it does:
    1. Fetch missing price data (auto-detects gaps — downloads only missing days)
    2. Run incremental backtest (skips if already done today)
    3. Start ngrok tunnel + set Telegram webhook
    4. Start Telegram bot server (always-on)
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("start_bot")

_ngrok_process = None
_bot_process = None


# ── Step 1: Fetch prices ────────────────────────────────────────────────────

def _step_fetch_prices():
    logger.info("📦 Step 1: Fetching missing price data…")
    result = subprocess.run(
        [sys.executable, "-m", "data.fetch_prices"],
        cwd="G:\\Stock Screener",
    )
    if result.returncode != 0:
        logger.warning("Price fetch had issues — continuing anyway.")
    else:
        logger.info("✅ Price data up-to-date.")


# ── Step 2: Backtest ────────────────────────────────────────────────────────

def _step_backtest(end_date: str, force: bool):
    if not force and _has_todays_run():
        logger.info("⏭️  Step 2: Backtest already run today — skipping.")
        return
    logger.info("⚙️  Step 2: Running incremental backtest…")
    cmd = [
        sys.executable, "run_backtest.py",
        "--incremental",
        "--end", end_date,
        "--run-name", "auto_bot",
        "--report",
    ]
    result = subprocess.run(cmd, cwd="G:\\Stock Screener")
    if result.returncode != 0:
        logger.error("❌ Backtest failed (exit code %d). Continuing anyway.", result.returncode)
    else:
        logger.info("✅ Backtest complete.")


# ── Step 3: ngrok tunnel ─────────────────────────────────────────────────────

def _step_delete_webhook():
    """Remove the webhook so getUpdates can be used during drain."""
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
        urllib.request.urlopen(url, timeout=5)
        logger.info("  Webhook deleted.")
    except Exception as e:
        logger.warning("  Delete webhook failed: %s", e)


def _step_drain_queue():
    """Process any messages sent while the bot was offline.
    Deleted messages are automatically excluded by Telegram's API.
    """
    logger.info("🗄️  Step 3: Draining queued messages…")
    _step_delete_webhook()
    result = subprocess.run(
        [sys.executable, "-m", "telegram_bot.server", "--drain"],
        cwd="G:\\Stock Screener",
        capture_output=True, text=True, timeout=30,
    )
    for line in result.stdout.splitlines():
        if "Drained" in line or "queued" in line or "Drain" in line or "No queued" in line:
            logger.info("  %s", line.strip())
    if result.returncode != 0:
        logger.warning("Drain had issues — continuing anyway.")


def _step_ngrok():
    logger.info("🌐 Step 4a: Starting ngrok tunnel…")
    global _ngrok_process

    _ngrok_process = subprocess.Popen(
        ["ngrok", "http", "8000", "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll ngrok API for public URL (usually ready in 2-3 sec)
    public_url = None
    for attempt in range(20):
        time.sleep(0.5)
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2)
            data = json.loads(resp.read())
            for tunnel in data.get("tunnels", []):
                if tunnel.get("public_url", "").startswith("https://"):
                    public_url = tunnel["public_url"]
                    break
        except Exception:
            pass
        if public_url:
            break

    if not public_url:
        logger.warning("ngrok tunnel not ready after 10s — continuing without public URL.")
        return None

    logger.info("  Public URL: %s", public_url)
    _set_webhook(public_url)
    return public_url


def _set_webhook(public_url: str):
    """Tell Telegram to forward updates to our ngrok URL."""
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping webhook setup.")
        return

    webhook_url = f"{public_url}/webhook"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"

    try:
        resp = urllib.request.urlopen(api_url, timeout=10)
        data = json.loads(resp.read())
        if data.get("ok"):
            logger.info("  Webhook set: ✓")
        else:
            logger.warning("  Webhook set failed: %s", data.get("description", "unknown"))
    except Exception as e:
        logger.warning("  Webhook set failed: %s", e)


# ── Step 4: Start bot ───────────────────────────────────────────────────────

def _step_start_bot():
    logger.info("🤖 Step 4b: Starting Telegram bot server on port 8000…")
    global _bot_process
    _free_port(8000)
    _bot_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "telegram_bot.server:app",
         "--host", "0.0.0.0",
         "--port", "8000"],
        cwd="G:\\Stock Screener",
    )
    _bot_process.wait()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_todays_run() -> bool:
    from db.database import get_connection
    today = date.today().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id FROM backtest_runs
               WHERE end_date >= ?
               ORDER BY created_at DESC LIMIT 1""",
            (today,),
        ).fetchone()
    return row is not None


def _free_port(port: int):
    """Kill any process currently listening on the given port."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and f"0.0.0.0:{port}" in parts[1]:
                pid = parts[4]
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=3)
                logger.info("  Freed port %d (killed PID %s)", port, pid)
    except Exception:
        pass


def _cleanup(signum=None, frame=None):
    logger.info("Shutting down…")
    if _ngrok_process:
        _ngrok_process.terminate()
        _ngrok_process.wait(timeout=3)
    if _bot_process:
        _bot_process.terminate()
        _bot_process.wait(timeout=5)
    sys.exit(0)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Single command: fetch prices → backtest → ngrok → start bot"
    )
    parser.add_argument("--force", action="store_true",
                        help="Re-run backtest even if already done today")
    parser.add_argument("--end",
                        default=date.today().isoformat(),
                        help="Backtest end date (default: today)")
    parser.add_argument("--no-ngrok", action="store_true",
                        help="Skip ngrok tunnel startup")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    _step_fetch_prices()
    _step_backtest(args.end, args.force)

    _step_drain_queue()

    if not args.no_ngrok:
        _step_ngrok()

    _step_start_bot()


if __name__ == "__main__":
    main()
