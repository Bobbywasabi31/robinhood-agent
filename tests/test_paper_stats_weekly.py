"""Weekly P&L breakdown tests for paper_stats.compute_stats.

by_week buckets dated trades by ISO week (YYYY-Www): weeks start Monday,
so a Sunday's trades join the week that Monday started, and the ISO year
is used at year boundaries (2028-01-01 lands in 2027-W52, where it
happened). Undated/unparseable trades go to "undated", mirroring by_month.
The breakdown appears in the text report ("Weekly P&L:"), the week CSV
section, and --json (carried automatically in the stats dict).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import compute_stats, format_csv, format_json, format_report


def week_trade(date, pnl, symbol="AAPL"):
    return {
        "date": date, "time": "09:31:00", "symbol": symbol, "side": "buy",
        "quantity": 1, "price": 100.0, "order_id": f"paper-{date}-{pnl}",
        "pnl": pnl, "paper": True,
    }


def test_by_week_groups_pnl_per_iso_week():
    # 2026-09-28 (Mon), 2026-10-03 (Sat), 2026-10-04 (Sun) -> 2026-W40;
    # 2026-10-05 (Mon), 2026-10-09 (Fri) -> 2026-W41.
    trades = [
        week_trade("2026-09-28", -5.0),
        week_trade("2026-10-03", 8.0),
        week_trade("2026-10-04", 0.0),   # flat: counts as a trade, no W/L
        week_trade("2026-10-05", 4.0),
        week_trade("2026-10-09", -2.0),
    ]
    by = compute_stats(trades)["by_week"]
    assert by["2026-W40"] == {"trades": 3, "pnl": 3.0, "wins": 1, "losses": 1}
    assert by["2026-W41"] == {"trades": 2, "pnl": 2.0, "wins": 1, "losses": 1}


def test_by_week_empty_and_undated():
    assert compute_stats([])["by_week"] == {}
    t = week_trade("2026-10-09", 7.0)
    del t["date"]
    s = compute_stats([t])
    assert s["by_week"]["undated"] == {"trades": 1, "pnl": 7.0, "wins": 1, "losses": 0}


def test_by_week_unparseable_date_counts_as_undated():
    t = week_trade("garbage", 2.0)
    s = compute_stats([t])
    assert s["by_week"]["undated"]["trades"] == 1


def test_by_week_iso_year_boundary_uses_iso_year():
    # 2027-12-31 (Fri) and 2028-01-01 (Sat) are both ISO 2027-W52:
    # the Saturday's calendar year differs, the ISO week does not.
    trades = [
        week_trade("2027-12-31", 5.0),
        week_trade("2028-01-01", -1.0),
    ]
    by = compute_stats(trades)["by_week"]
    assert list(by) == ["2027-W52"]
    assert by["2027-W52"] == {"trades": 2, "pnl": 4.0, "wins": 1, "losses": 1}


def test_by_week_sunday_joins_the_week_monday_started():
    # A Sunday alone does not start a new bucket: 2026-10-04 (Sun)
    # joins 2026-09-28 (Mon) in 2026-W40, not 2026-10-05 (Mon) in W41.
    trades = [
        week_trade("2026-09-28", 1.0),
        week_trade("2026-10-04", 2.0),
    ]
    by = compute_stats(trades)["by_week"]
    assert list(by) == ["2026-W40"]
    assert by["2026-W40"]["trades"] == 2


def test_format_report_includes_weekly_pnl_between_monthly_and_daily():
    trades = [
        week_trade("2026-09-30", 8.0),
        week_trade("2026-10-01", 4.0),
        week_trade("2026-10-06", -2.0),
    ]
    report = format_report(compute_stats(trades))
    assert "Weekly P&L:" in report
    # 2026-09-30 (Wed) + 2026-10-01 (Thu) -> 2026-W40; 2026-10-06 (Tue) -> W41.
    assert report.index("2026-W40: $12.00 over 2 trades (2W/0L)") < \
        report.index("2026-W41: $-2.00 over 1 trades (0W/1L)")
    # Coarse-to-fine ordering: Monthly, Weekly, Daily.
    assert report.index("Monthly P&L:") < report.index("Weekly P&L:") < \
        report.index("Daily P&L:")


def test_by_week_json_and_csv_carry():
    trades = [
        week_trade("2026-09-30", 8.0),
        week_trade("2026-10-06", -2.0),
    ]
    s = compute_stats(trades)
    payload = json.loads(format_json(s))
    assert payload["stats"]["by_week"]["2026-W40"]["pnl"] == 8.0
    assert payload["stats"]["by_week"]["2026-W41"]["pnl"] == -2.0
    csv_lines = format_csv(s).strip().splitlines()
    week_rows = [ln for ln in csv_lines if ln.startswith("week,")]
    assert week_rows == ["week,2026-W40,1,1,0,8.00", "week,2026-W41,1,0,1,-2.00"]


def test_by_week_undated_sorts_last_in_report_and_csv():
    t = week_trade("2026-10-06", -2.0)
    undated = week_trade("2026-10-06", 3.0)
    del undated["date"]
    report = format_report(compute_stats([t, undated]))
    week_lines = [ln for ln in report.splitlines() if "W4" in ln or "undated:" in ln]
    assert week_lines[-1].strip().startswith("undated:")
    csv_lines = format_csv(compute_stats([t, undated])).strip().splitlines()
    week_rows = [ln for ln in csv_lines if ln.startswith("week,")]
    assert week_rows[-1] == "week,undated,1,1,0,3.00"
