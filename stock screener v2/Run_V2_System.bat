@echo off
title V2 Swing Trading & Screener System
echo ====================================================
echo      V2 SWING TRADING SYSTEM - FULLY AUTOMATED
echo ====================================================
echo.
echo Starting Unified Master Dashboard...
echo - Live Paper Trader: ACTIVE (Monitors 1m candles)
echo - Telegram Bot Listener: ACTIVE (24/7 Command & Square Off)
echo - Auto Screener Scheduler: ACTIVE (Runs Daily at 4:00 PM)
echo.
cd /d "g:\Stock Screener\stock screener v2"
streamlit run dashboard.py
pause
