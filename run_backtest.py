#!/usr/bin/env python3
"""
run_backtest.py — Entry point for the backtesting engine.

Usage:
    python run_backtest.py --start 2020-01-01 --end 2025-12-31 --run-name "v2_tiered_regime"
    python run_backtest.py --walk-forward --start 2020-01-01 --end 2025-12-31 --report
    python run_backtest.py --start 2024-01-01 --end 2025-12-31 --report --report-file report.md
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.cli import main

if __name__ == "__main__":
    main()
