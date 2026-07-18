#!/usr/bin/env python3
"""
start_all.py — Unified Stock Screener Launcher.

Boots:
    1. Port cleaning (8000, 8501)
    2. Streamlit dashboard (port 8501) in background
    3. Ingestion & Indicator generation for all NSE stocks
    4. Self-Improving optimization backtests (once per week or forced)
    5. Daily screener pipeline (using optimized parameters)
    6. Sends screener picks and backtest reports to Telegram
    7. ngrok tunnel + webhook setup
    8. FastAPI Telegram bot (port 8000)

Usage:
    python start_all.py
    python start_all.py --force-opt   # force parameter optimization run
    python start_all.py --dry-run     # run pipeline in dry-run mode (no db writes/Telegram)
"""

import sys
import os
import time
import json
import signal
import logging
import argparse
import subprocess
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# Ensure workspace is in python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db.database import init_db, get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("start_all")

# Global process tracking for cleanup
_streamlit_process = None
_ngrok_process = None
_bot_process = None


# ── Step 1: Port Cleanup ─────────────────────────────────────────────────────

def _free_port(port: int):
    """Kill any process listening on the given port (Windows compatible)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and f"0.0.0.0:{port}" in parts[1]:
                pid = parts[4]
                logger.info("Freeing port %d (killing PID %s)...", port, pid)
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=3
                )
    except Exception as e:
        logger.debug("Port cleanup failed for %d: %s", port, e)


# ── Step 2: Ingestion ────────────────────────────────────────────────────────

def _run_ingestion():
    logger.info("📦 Step 1: Fetching EOD data (NSE Bhavcopy / yfinance fallback)...")
    result = subprocess.run(
        [sys.executable, "-m", "data.fetch_prices"],
        cwd="G:\\Stock Screener",
    )
    if result.returncode != 0:
        logger.warning("Price fetch finished with warnings.")
    else:
        logger.info("✅ Price data up to date.")


# ── Step 3: Self-Improving Optimization ──────────────────────────────────────

def _needs_optimization() -> bool:
    """Return True if no optimization has run in the last 7 days."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) as mca FROM backtest_walkforward_windows"
            ).fetchone()
        if row and row["mca"]:
            last_opt = datetime.strptime(row["mca"].split(".")[0], "%Y-%m-%d %H:%M:%S")
            return datetime.now() - last_opt > timedelta(days=7)
    except Exception:
        pass
    return True


def _run_optimization(force: bool):
    if not force and not _needs_optimization():
        logger.info("⏭️  Step 2: Self-optimising backtester already run recently — skipping optimization.")
        return

    logger.info("⚙️  Step 2: Running self-improving parameter optimization (Walk-Forward sweep)...")
    
    # Run walk-forward optimization for the last 18 months to find best current params
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=550)).isoformat()
    
    cmd = [
        sys.executable, "run_backtest.py",
        "--walk-forward",
        "--start", start_date,
        "--end", end_date,
        "--report",
    ]
    
    result = subprocess.run(cmd, cwd="G:\\Stock Screener", capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("❌ Self-optimization failed. Using defaults.")
        logger.error(result.stderr)
    else:
        logger.info("✅ Self-optimization completed.")
        
        # Extract and send walk-forward report to Telegram
        report = result.stdout
        _send_backtest_report_to_telegram(report)


def _send_backtest_report_to_telegram(report: str):
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    # Truncate report if too long for Telegram (max 4096 characters)
    title = "📊 *Self-Improving Backtester Report*\n\n"
    body = report
    if len(title + body) > 4000:
        body = body[:3800] + "\n\n...[Truncated due to length]..."
        
    msg = f"{title}```\n{body}\n```"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}).encode(),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("✅ Optimization report sent to Telegram.")
    except Exception as e:
        logger.warning("Failed to send report to Telegram: %s", e)


# ── Step 4: Daily Screener Pipeline ──────────────────────────────────────────

def _run_screener(dry_run: bool):
    logger.info("🔍 Step 3: Running daily screener pipeline...")
    cmd = [sys.executable, "main.py"]
    if dry_run:
        cmd.append("--dry-run")
        
    result = subprocess.run(cmd, cwd="G:\\Stock Screener")
    if result.returncode != 0:
        logger.error("❌ Daily screener pipeline failed.")
    else:
        logger.info("✅ Daily screener complete.")


# ── Step 5: Start Streamlit Dashboard ────────────────────────────────────────

def _start_dashboard():
    logger.info("🖥️  Step 4: Starting Streamlit dashboard on port 8501...")
    global _streamlit_process
    _free_port(8501)
    
    log_dir = "G:\\Stock Screener\\run_logging"
    os.makedirs(log_dir, exist_ok=True)
    log_file = open(os.path.join(log_dir, "streamlit.log"), "w", encoding="utf-8")
    
    _streamlit_process = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "dashboard/app.py",
            "--server.port", "8501",
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false"
        ],
        cwd="G:\\Stock Screener",
        stdout=log_file,
        stderr=log_file,
    )


# ── Step 6: ngrok Tunnel & Webhook Setup ─────────────────────────────────────

def _start_ngrok() -> str:
    logger.info("🌐 Step 5a: Starting ngrok tunnel...")
    global _ngrok_process

    _ngrok_process = subprocess.Popen(
        ["ngrok", "http", "8000", "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

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
        logger.warning("ngrok tunnel not ready after 10s.")
        return None

    logger.info("  Public URL: %s", public_url)
    _set_webhook(public_url)
    return public_url


def _set_webhook(public_url: str):
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
            logger.info("  Telegram webhook configured successfully.")
        else:
            logger.warning("  Webhook setup failed: %s", data.get("description"))
    except Exception as e:
        logger.warning("  Webhook setup failed: %s", e)


# ── Step 7: Start Telegram Bot ───────────────────────────────────────────────

def _start_bot():
    logger.info("🤖 Step 5b: Starting FastAPI bot server on port 8000...")
    global _bot_process
    _free_port(8000)
    
    _bot_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "telegram_bot.server:app",
         "--host", "0.0.0.0",
         "--port", "8000"],
        cwd="G:\\Stock Screener",
    )


# ── Cleanup ──────────────────────────────────────────────────────────────────

def _cleanup(signum=None, frame=None):
    logger.info("Shutting down unified components...")
    
    if _streamlit_process:
        logger.info("Terminating Streamlit dashboard process...")
        _streamlit_process.terminate()
        _streamlit_process.wait(timeout=3)
        
    if _ngrok_process:
        logger.info("Terminating ngrok tunnel process...")
        _ngrok_process.terminate()
        _ngrok_process.wait(timeout=3)
        
    if _bot_process:
        logger.info("Terminating Telegram bot server...")
        _bot_process.terminate()
        _bot_process.wait(timeout=5)
        
    sys.exit(0)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Unified launcher: Dashboard, Data Ingestion, Self-Improving Backtest, and Telegram bot webhook"
    )
    parser.add_argument("--force-opt", action="store_true",
                        help="Force parameter optimization sweep")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run daily pipeline without DB writes or Telegram updates")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    # Initialize SQLite tables
    init_db()

    # Start Dashboard
    _start_dashboard()

    # Fetch daily prices
    _run_ingestion()

    # Self-optimizing parameter search
    _run_optimization(args.force_opt)

    # Daily screening execution
    _run_screener(args.dry_run)

    # Webhook setup + Bot server
    if not args.dry_run:
        _start_ngrok()
        _start_bot()
        
        # Keep main thread alive
        logger.info("🚀 System fully online. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    else:
        logger.info("Dry-run execution complete.")
        _cleanup()


if __name__ == "__main__":
    main()
