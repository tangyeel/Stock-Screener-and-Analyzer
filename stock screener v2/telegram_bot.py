"""
Telegram Bot Module (Re-exports from unified dashboard.py)
"""
import os
import sys

V2_DIR = os.path.dirname(os.path.abspath(__file__))
if V2_DIR not in sys.path:
    sys.path.insert(0, V2_DIR)

try:
    from dashboard import (
        get_telegram_config,
        send_telegram_message,
        send_telegram_message_with_keyboard,
        send_daily_picks_alert,
        send_trade_event_alert,
        analyze_stock_for_telegram,
        generate_detailed_status_and_menu,
        square_off_trade_from_telegram,
        run_telegram_listener
    )
except Exception as e:
    print("Warning re-exporting Telegram helpers from dashboard:", e)

if __name__ == "__main__":
    run_telegram_listener()
