"""
notifications/telegram.py — Telegram message delivery.

Sends messages via the Telegram Bot API and logs every attempt
to telegram_log (success or failure). Uses requests with a timeout
so a Telegram outage doesn't hang the entire pipeline.
"""

import uuid
import logging
from datetime import datetime

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db.database import get_connection

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 15  # seconds


def send_telegram_message(text: str, run_id: str = None, parse_mode: str = "Markdown") -> bool:
    """
    Send a message to the configured Telegram chat.

    Args:
        text:       Message text (Markdown formatted).
        run_id:     Current run UUID for logging (optional).
        parse_mode: Telegram parse mode — "Markdown" or "HTML".

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error(
            "Telegram credentials not configured — "
            "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        )
        _log_telegram(run_id, text, success=False, api_response="credentials_missing")
        return False

    url = _TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        success = resp.status_code == 200

        if not success:
            logger.error(
                "Telegram API error %d: %s",
                resp.status_code, resp.text[:300],
            )

        _log_telegram(run_id, text, success=success, api_response=resp.text[:1000])
        return success

    except requests.exceptions.Timeout:
        logger.error("Telegram request timed out after %ds", _TIMEOUT)
        _log_telegram(run_id, text, success=False, api_response="timeout")
        return False

    except requests.exceptions.RequestException as e:
        logger.error("Telegram request failed: %s", e)
        _log_telegram(run_id, text, success=False, api_response=str(e)[:500])
        return False


def _log_telegram(run_id: str, message_text: str, success: bool, api_response: str) -> None:
    """Write Telegram delivery attempt to telegram_log."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO telegram_log (id, run_id, message_text, success, api_response, sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    message_text[:4000],   # Telegram limit is 4096 chars
                    1 if success else 0,
                    api_response,
                    datetime.utcnow().isoformat(),
                ),
            )
    except Exception as e:
        # Never let logging failures cascade into pipeline failures
        logger.warning("Failed to write telegram_log: %s", e)
