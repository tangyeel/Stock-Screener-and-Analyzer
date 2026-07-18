"""
jobs/analyze_picks.py — Analyze screener picks and their outcomes.

Usage:
    python jobs/analyze_picks.py              # summary stats
    python jobs/analyze_picks.py --picks       # per-pick detail table
    python jobs/analyze_picks.py --sectors     # sector breakdown
    python jobs/analyze_picks.py --csv         # CSV export (all picks + outcomes)
    python jobs/analyze_picks.py --picks --csv # per-pick detail as CSV
    python jobs/analyze_picks.py --date 2026-07-10  # filter by screen date
    python jobs/analyze_picks.py --days 30          # last N days

All data read from screener.db — no yfinance calls.
"""

import sys
import csv
import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Allow Unicode output even on Windows cp1252 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from db.database import get_connection, init_db

logging.basicConfig(level=logging.WARNING)

STATUS_EMOJI = {
    "pending":     "[-]",
    "triggered":   "[>]",
    "stopped_out": "[X]",
    "target_hit":  "[O]",
    "expired":     "[~]",
}


def fetch_picks(screen_date: str = None, days: int = None) -> list[dict]:
    init_db()
    conditions = []
    params = []

    if screen_date:
        conditions.append("ds.screen_date = ?")
        params.append(screen_date)
    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        conditions.append("ds.screen_date >= ?")
        params.append(cutoff)

    where = " AND ".join(conditions) if conditions else "1"
    q = f"""
        SELECT
            ds.id,
            ds.screen_date,
            ds.symbol,
            nc.sector,
            ds.setup_type,
            ds.entry,
            ds.stop,
            ds.target,
            ds.risk_pct,
            ds.reward_risk_ratio,
            ds.rs_rating,
            ds.sector_rs_rating,
            ds.shares_suggested,
            ds.market_regime,
            ds.regime_tier,
            ds.is_override,
            COALESCE(to2.status, 'pending') AS outcome_status,
            to2.triggered_at,
            to2.closed_at,
            to2.exit_price,
            to2.pnl_pct,
            to2.days_held
        FROM daily_screens ds
        LEFT JOIN trade_outcomes to2 ON ds.id = to2.screen_id
        LEFT JOIN nifty100_constituents nc ON ds.symbol = nc.symbol
        WHERE {where}
        ORDER BY ds.screen_date DESC, ds.symbol
    """
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def print_summary(picks: list[dict]):
    total = len(picks)
    if total == 0:
        print("No picks found.")
        return

    triggered = sum(1 for p in picks if p["outcome_status"] in ("triggered", "stopped_out", "target_hit"))
    stopped   = sum(1 for p in picks if p["outcome_status"] == "stopped_out")
    hit_target = sum(1 for p in picks if p["outcome_status"] == "target_hit")
    expired   = sum(1 for p in picks if p["outcome_status"] == "expired")
    pending   = sum(1 for p in picks if p["outcome_status"] == "pending")

    closed = [p for p in picks if p["outcome_status"] in ("stopped_out", "target_hit", "expired") and p["pnl_pct"] is not None]
    pnls = [p["pnl_pct"] for p in closed]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    winners = [p for p in closed if p["pnl_pct"] > 0]
    losers  = [p for p in closed if p["pnl_pct"] <= 0]
    win_rate = len(winners) / len(closed) * 100 if closed else 0.0

    rrs = [p["reward_risk_ratio"] for p in picks if p["reward_risk_ratio"]]
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.0

    # Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    avg_win = sum(p["pnl_pct"] for p in winners) / len(winners) if winners else 0.0
    avg_loss = abs(sum(p["pnl_pct"] for p in losers) / len(losers)) if losers else 0.0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss) if closed else 0.0

    override_count = sum(1 for p in picks if p.get("is_override"))

    print("=" * 50)
    print("  PICK ANALYSIS REPORT")
    print("=" * 50)
    print(f"  Total picks:        {total}")
    print(f"  Date range:         {picks[-1]['screen_date']} – {picks[0]['screen_date']}" if len(picks) > 1 else f"  Date:               {picks[0]['screen_date']}")
    print(f"  Bearish overrides:  {override_count}")
    print()
    print(f"  Status breakdown:")
    print(f"    Pending           {pending}")
    print(f"    Triggered         {triggered}")
    print(f"    Stopped out       {stopped}")
    print(f"    Target hit        {hit_target}")
    print(f"    Expired           {expired}")
    print()
    print(f"  Closed trades:      {len(closed)}")
    print(f"  Win rate:           {win_rate:.1f}%")
    print(f"  Avg P&L:            {avg_pnl:+.2f}%")
    print(f"  Avg winner:         {avg_win:+.2f}%")
    print(f"  Avg loser:          {avg_loss:.2f}%")
    print(f"  Best trade:         {max(pnls):+.2f}%" if pnls else "  Best trade:         —")
    print(f"  Worst trade:        {min(pnls):+.2f}%" if pnls else "  Worst trade:        —")
    print(f"  Avg R:R             {avg_rr:.1f}:1")
    print(f"  Expectancy:         {expectancy:+.2f}% per trade")
    print("=" * 50)


def print_picks(picks: list[dict], as_csv: bool = False):
    if not picks:
        print("No picks found.")
        return

    if as_csv:
        writer = csv.writer(sys.stdout)
        writer.writerow([
            "screen_date", "symbol", "sector", "setup_type", "entry", "stop", "target",
            "risk_pct", "rr", "rs_rating", "sector_rs", "shares",
            "regime", "override", "outcome", "pnl_pct", "days_held",
        ])
        for p in picks:
            writer.writerow([
                p["screen_date"], p["symbol"], p.get("sector", ""), p["setup_type"],
                _f(p["entry"]), _f(p["stop"]), _f(p["target"]),
                _f(p["risk_pct"]), _f(p["reward_risk_ratio"]),
                p["rs_rating"], p["sector_rs_rating"], p["shares_suggested"],
                p["regime_tier"], p["is_override"],
                p["outcome_status"], _f(p["pnl_pct"]), p["days_held"],
            ])
        return

    print(f"{'Date':<12} {'Symbol':<12} {'Setup':<10} {'Entry':>10} {'Stop':>10} {'Target':>10} {'RS':>4} {'SctRS':>4} {'Status':<12} {'P&L':>8} {'Days':>4}")
    print("-" * 100)

    for p in picks:
        emoji = STATUS_EMOJI.get(p["outcome_status"], "?")
        status_str = f"{emoji} {p['outcome_status']}"
        pnl = f"{p['pnl_pct']:+.2f}%" if p["pnl_pct"] is not None else " —"
        print(
            f"{p['screen_date']:<12} {p['symbol']:<12} {p['setup_type']:<10} "
            f"{_f(p['entry']):>10} {_f(p['stop']):>10} {_f(p['target']):>10} "
            f"{p['rs_rating']:>4} {p['sector_rs_rating']:>4} "
            f"{status_str:<12} {pnl:>8} {p['days_held'] or '—':>4}"
        )

    print("-" * 100)


def print_sectors(picks: list[dict]):
    if not picks:
        print("No picks found.")
        return

    sectors = {}
    for p in picks:
        s = p.get("sector") or "Unknown"
        if s not in sectors:
            sectors[s] = {"total": 0, "stopped": 0, "target_hit": 0, "expired": 0, "pending": 0}
        sectors[s]["total"] += 1
        status = p["outcome_status"]
        if status in sectors[s]:
            sectors[s][status] += 1

    print(f"{'Sector':<35} {'Picks':>5} {'Stop%':>6} {'Target%':>8} {'Pending':>7}")
    print("-" * 65)

    for s in sorted(sectors, key=lambda x: sectors[x]["total"], reverse=True):
        d = sectors[s]
        stop_pct = d["stopped"] / d["total"] * 100 if d["total"] else 0
        hit_pct  = d["target_hit"] / d["total"] * 100 if d["total"] else 0
        print(
            f"{s:<35} {d['total']:>5} "
            f"{stop_pct:>5.0f}% {hit_pct:>6.0f}% "
            f"{d['pending']:>7}"
        )

    print("-" * 65)


def _f(v) -> str:
    if v is None:
        return ""
    return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze screener picks and outcomes")
    parser.add_argument("--picks", action="store_true", help="Show per-pick detail table")
    parser.add_argument("--sectors", action="store_true", help="Show sector breakdown")
    parser.add_argument("--csv", action="store_true", help="Export as CSV")
    parser.add_argument("--date", help="Filter by screen date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Last N days")
    args = parser.parse_args()

    picks = fetch_picks(screen_date=args.date, days=args.days)

    if args.csv and not args.picks:
        print_summary(picks)
        print()
        print("─" * 40)
        print("CSV export (all picks):")
        print("─" * 40)
        print_picks(picks, as_csv=True)
    elif args.picks:
        print_picks(picks, as_csv=args.csv)
    elif args.sectors:
        print_sectors(picks)
    else:
        print_summary(picks)
