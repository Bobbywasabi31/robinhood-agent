"""Sharpe-ratio (annualized) tests for paper_stats.compute_stats.

The ratio is mean(daily P&L) / sample-stdev(daily P&L) * sqrt(252), over
the journal's dated day buckets (undated trades excluded). None when
fewer than 2 dated trading days or zero variance.
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


def test_sharpe_ratio_basics():
    # Daily P&L [1, 2, 3]: mean 2, sample stdev 1 -> 2 * sqrt(252) = 31.749... -> 31.75
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0),
            day_trade("2026-10-09", 3.0)]
    s = compute_stats(rows)
    assert s["sharpe_days"] == 3
    assert s["sharpe_ratio"] == 31.75
    out = format_report(s)
    assert "  Sharpe ratio (ann): 31.75" in out


def test_sharpe_ratio_negative():
    rows = [day_trade("2026-10-07", -1.0), day_trade("2026-10-08", -2.0),
            day_trade("2026-10-09", -3.0)]
    s = compute_stats(rows)
    assert s["sharpe_ratio"] == -31.75


def test_sharpe_ratio_none_single_day():
    s = compute_stats([day_trade("2026-10-09", 10.0)])
    assert s["sharpe_days"] == 1
    assert s["sharpe_ratio"] is None
    assert "  Sharpe ratio (ann): n/a" in format_report(s)


def test_sharpe_ratio_none_zero_variance():
    rows = [day_trade("2026-10-07", 5.0), day_trade("2026-10-08", 5.0),
            day_trade("2026-10-09", 5.0)]
    s = compute_stats(rows)
    assert s["sharpe_days"] == 3
    assert s["sharpe_ratio"] is None


def test_sharpe_ratio_none_empty():
    s = compute_stats([])
    assert s["sharpe_days"] == 0
    assert s["sharpe_ratio"] is None
    assert "  Sharpe ratio (ann): n/a" in format_report(s)


def test_sharpe_ratio_excludes_undated():
    rows = [
        day_trade("2026-10-08", 1.0),
        day_trade("2026-10-09", 3.0),
        {**day_trade("2026-10-09", 100.0), "date": None},  # undated: ignored
        {**day_trade("2026-10-09", -100.0), "date": "garbage"},  # unparseable: ignored
    ]
    s = compute_stats(rows)
    # Only the two dated days count: [1, 3] -> mean 2, stdev sqrt(2) -> 2/sqrt(2)*sqrt(252)
    assert s["sharpe_days"] == 2
    assert s["sharpe_ratio"] == round(2 / 2 ** 0.5 * 252 ** 0.5, 2)
    assert s["sharpe_ratio"] == 22.45


def test_sharpe_ratio_multiple_trades_net_per_day():
    # Two trades the same day net to one daily observation: [5, 15]
    rows = [day_trade("2026-10-08", 2.0), day_trade("2026-10-08", 3.0),
            day_trade("2026-10-09", 15.0)]
    s = compute_stats(rows)
    assert s["sharpe_days"] == 2
    # mean 10, stdev sqrt(50) -> 10/sqrt(50)*sqrt(252) = 22.45
    assert s["sharpe_ratio"] == 22.45


def test_sharpe_ratio_json_and_csv_carry():
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0),
            day_trade("2026-10-09", 3.0)]
    s = compute_stats(rows)
    payload = json.loads(format_json(s))
    assert payload["stats"]["sharpe_ratio"] == 31.75
    assert payload["stats"]["sharpe_days"] == 3
    lines = format_csv(s).strip().splitlines()
    risk = [ln for ln in lines if ln.startswith("risk,")]
    assert risk == ["risk,sharpe_ratio_ann,3,,,31.75"]
    # The pnl column stays plain-decimal for spreadsheet parsers.
    assert float(risk[0].split(",")[-1]) == 31.75


def test_sharpe_ratio_csv_omitted_when_undefined():
    s = compute_stats([day_trade("2026-10-09", 10.0)])
    lines = format_csv(s).strip().splitlines()
    assert not [ln for ln in lines if ln.startswith("risk,")]
