"""Quarterly P&L breakdown tests for paper_stats.compute_stats.

by_quarter buckets dated trades by calendar quarter (YYYY-Qn):
Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec.
Undated/unparseable trades go to "undated", mirroring by_month/by_week.
The breakdown appears in the text report ("Quarterly P&L:", before the
monthly section), the quarter CSV section, and --json (carried
automatically in the stats dict).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import compute_stats, format_csv, format_json, format_report


def quarter_trade(date, pnl, symbol="AAPL"):
    return {
        "date": date, "time": "09:31:00", "symbol": symbol, "side": "buy",
        "quantity": 1, "price": 100.0, "order_id": f"paper-{date}-{pnl}",
        "pnl": pnl, "paper": True,
    }


def test_by_quarter_groups_pnl_per_calendar_quarter():
    # 2026-03-31 -> Q1; 2026-04-01 + 2026-06-30 -> Q2;
    # 2026-09-30 -> Q3; 2026-10-01 + 2026-12-31 -> Q4.
    trades = [
        quarter_trade("2026-03-31", 5.0),
        quarter_trade("2026-04-01", -2.0),
        quarter_trade("2026-06-30", 8.0),
        quarter_trade("2026-09-30", 1.0),
        quarter_trade("2026-10-01", 4.0),
        quarter_trade("2026-12-31", 0.0),  # flat: counts as a trade, no W/L
    ]
    by = compute_stats(trades)["by_quarter"]
    assert by["2026-Q1"] == {"trades": 1, "pnl": 5.0, "wins": 1, "losses": 0}
    assert by["2026-Q2"] == {"trades": 2, "pnl": 6.0, "wins": 1, "losses": 1}
    assert by["2026-Q3"] == {"trades": 1, "pnl": 1.0, "wins": 1, "losses": 0}
    assert by["2026-Q4"] == {"trades": 2, "pnl": 4.0, "wins": 1, "losses": 0}


def test_by_quarter_empty_and_undated():
    assert compute_stats([])["by_quarter"] == {}
    t = quarter_trade("2026-10-09", 7.0)
    del t["date"]
    s = compute_stats([t])
    assert s["by_quarter"]["undated"] == {"trades": 1, "pnl": 7.0, "wins": 1, "losses": 0}


def test_by_quarter_unparseable_date_counts_as_undated():
    t = quarter_trade("garbage", 2.0)
    s = compute_stats([t])
    assert s["by_quarter"]["undated"]["trades"] == 1


def test_by_quarter_year_boundary_starts_a_new_bucket():
    # 2026-12-31 (Q4) and 2027-01-01 (Q1): a new year starts a new quarter.
    trades = [
        quarter_trade("2026-12-31", 5.0),
        quarter_trade("2027-01-01", -1.0),
    ]
    by = compute_stats(trades)["by_quarter"]
    assert list(by) == ["2026-Q4", "2027-Q1"]
    assert by["2026-Q4"] == {"trades": 1, "pnl": 5.0, "wins": 1, "losses": 0}
    assert by["2027-Q1"] == {"trades": 1, "pnl": -1.0, "wins": 0, "losses": 1}


def test_format_report_includes_quarterly_pnl_before_monthly():
    trades = [
        quarter_trade("2026-09-30", 8.0),
        quarter_trade("2026-10-01", 4.0),
        quarter_trade("2026-10-06", -2.0),
    ]
    report = format_report(compute_stats(trades))
    assert "Quarterly P&L:" in report
    # 2026-09-30 -> 2026-Q3; 2026-10-01 + 2026-10-06 -> 2026-Q4.
    assert report.index("2026-Q3: $8.00 over 1 trades (1W/0L)") < \
        report.index("2026-Q4: $2.00 over 2 trades (1W/1L)")
    # Coarse-to-fine ordering: Quarterly, Monthly, Weekly, Daily.
    assert report.index("Quarterly P&L:") < report.index("Monthly P&L:") < \
        report.index("Weekly P&L:") < report.index("Daily P&L:")


def test_by_quarter_json_and_csv_carry():
    trades = [
        quarter_trade("2026-09-30", 8.0),
        quarter_trade("2026-10-06", -2.0),
    ]
    s = compute_stats(trades)
    payload = json.loads(format_json(s))
    assert payload["stats"]["by_quarter"]["2026-Q3"]["pnl"] == 8.0
    assert payload["stats"]["by_quarter"]["2026-Q4"]["pnl"] == -2.0
    csv_lines = format_csv(s).strip().splitlines()
    quarter_rows = [ln for ln in csv_lines if ln.startswith("quarter,")]
    assert quarter_rows == ["quarter,2026-Q3,1,1,0,8.00", "quarter,2026-Q4,1,0,1,-2.00"]


def test_by_quarter_undated_sorts_last_in_report_and_csv():
    t = quarter_trade("2026-10-06", -2.0)
    undated = quarter_trade("2026-10-06", 3.0)
    del undated["date"]
    report = format_report(compute_stats([t, undated]))
    # Scope to the Quarterly section only: the day/month/week sections
    # emit their own "undated:" lines further down the report.
    section = report.split("Quarterly P&L:")[1].split("Monthly P&L:")[0]
    section_lines = [ln for ln in section.splitlines() if ln.strip()]
    assert section_lines[-1].strip().startswith("undated:")
    csv_lines = format_csv(compute_stats([t, undated])).strip().splitlines()
    quarter_rows = [ln for ln in csv_lines if ln.startswith("quarter,")]
    assert quarter_rows[-1] == "quarter,undated,1,1,0,3.00"
