"""Calmar-ratio (annualized) tests for paper_stats.compute_stats.

The ratio is mean(daily P&L) * 252 / max-drawdown-of-the-cumulative-daily-
P&L-curve, over the journal's dated day buckets (undated trades excluded,
same series as Sharpe/Sortino). Positive means the stream earns its worst
drawdown back that many times per year; negative means the stream is
losing. None when fewer than 2 dated trading days or no drawdown at all
(dd == 0 has no defined ratio).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import compute_stats, format_csv, format_json, format_report


def day_trade(date, pnl, symbol="AAPL"):
    return {
        "date": date, "time": "09:31:00", "symbol": symbol, "side": "buy",
        "quantity": 1, "price": 100.0, "order_id": f"paper-{date}-{pnl}",
        "pnl": pnl, "paper": True,
    }


def expected_calmar(dailies):
    mean = sum(dailies) / len(dailies)
    cum, peak, dd = 0.0, 0.0, 0.0
    for p in dailies:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(mean * 252 / dd, 2)


def test_calmar_ratio_basics():
    # Daily P&L [4, -2, -1]: cumulative [4, 2, 1], worst drawdown 3,
    # mean 1/3 -> (1/3 * 252) / 3 = 28.0.
    rows = [day_trade("2026-10-07", 4.0), day_trade("2026-10-08", -2.0),
            day_trade("2026-10-09", -1.0)]
    s = compute_stats(rows)
    assert s["calmar_days"] == 3
    assert s["calmar_ratio"] == expected_calmar([4.0, -2.0, -1.0]) == 28.0
    assert "  Calmar ratio (ann): 28.00" in format_report(s)


def test_calmar_ratio_negative_when_stream_loses():
    # Daily P&L [-4, 1, -1]: cumulative [-4, -3, -4], drawdown 4 from 0,
    # mean -4/3 -> (-4/3 * 252) / 4 = -84.0. Defined, not None.
    rows = [day_trade("2026-10-07", -4.0), day_trade("2026-10-08", 1.0),
            day_trade("2026-10-09", -1.0)]
    s = compute_stats(rows)
    assert s["calmar_ratio"] == expected_calmar([-4.0, 1.0, -1.0]) == -84.0
    assert "  Calmar ratio (ann): -84.00" in format_report(s)


def test_calmar_ratio_none_when_no_drawdown():
    # Every day up: the cumulative curve never falls below its peak.
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0),
            day_trade("2026-10-09", 3.0)]
    s = compute_stats(rows)
    assert s["calmar_days"] == 3
    assert s["calmar_ratio"] is None
    assert "  Calmar ratio (ann): n/a" in format_report(s)


def test_calmar_ratio_none_single_day():
    s = compute_stats([day_trade("2026-10-09", -10.0)])
    assert s["calmar_days"] == 1
    assert s["calmar_ratio"] is None
    assert "  Calmar ratio (ann): n/a" in format_report(s)


def test_calmar_ratio_excludes_undated():
    rows = [
        day_trade("2026-10-07", 4.0),
        day_trade("2026-10-08", -2.0),
        day_trade("2026-10-09", -1.0),
        {**day_trade("2026-10-09", -100.0), "date": None},  # undated: ignored
    ]
    s = compute_stats(rows)
    assert s["calmar_days"] == 3
    assert s["calmar_ratio"] == expected_calmar([4.0, -2.0, -1.0])


def test_calmar_ratio_zero_pnl_day_counts():
    # A flat day joins the series (counts toward calmar_days) and can
    # deepen the drawdown without changing the mean numerator's sign.
    rows = [day_trade("2026-10-07", 2.0), day_trade("2026-10-08", -3.0),
            day_trade("2026-10-09", 0.0)]
    s = compute_stats(rows)
    assert s["calmar_days"] == 3
    assert s["calmar_ratio"] == expected_calmar([2.0, -3.0, 0.0])


def test_calmar_ratio_json_and_csv_carry():
    rows = [day_trade("2026-10-07", 4.0), day_trade("2026-10-08", -2.0),
            day_trade("2026-10-09", -1.0)]
    s = compute_stats(rows)
    payload = json.loads(format_json(s))
    assert payload["stats"]["calmar_ratio"] == s["calmar_ratio"]
    assert payload["stats"]["calmar_days"] == 3
    lines = format_csv(s).strip().splitlines()
    risk = [ln for ln in lines if ln.startswith("risk,")]
    assert risk == [f"risk,sharpe_ratio_ann,3,,,{s['sharpe_ratio']:.2f}",
                    f"risk,sortino_ratio_ann,3,,,{s['sortino_ratio']:.2f}",
                    f"risk,calmar_ratio_ann,3,,,{s['calmar_ratio']:.2f}"]


def test_calmar_ratio_csv_omitted_when_undefined():
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0)]
    s = compute_stats(rows)
    lines = format_csv(s).strip().splitlines()
    assert not [ln for ln in lines if ln.startswith("risk,calmar")]
