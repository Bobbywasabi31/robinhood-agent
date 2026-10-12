"""Sortino-ratio (annualized) tests for paper_stats.compute_stats.

The ratio is mean(daily P&L) / sample-stdev(losing daily P&Ls only)
* sqrt(252), over the journal's dated day buckets (undated trades
excluded). Upside volatility is not penalized. None when fewer than 2
dated trading days, fewer than 2 losing days, or zero downside variance.
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


def expected_sortino(dailies):
    mean = sum(dailies) / len(dailies)
    down = [p for p in dailies if p < 0]
    dmean = sum(down) / len(down)
    dvar = sum((p - dmean) ** 2 for p in down) / (len(down) - 1)
    return round(mean / dvar ** 0.5 * 252 ** 0.5, 2)


def test_sortino_ratio_basics():
    # Daily P&L [4, -1, -2]: mean 1/3, downside stdev of [-1, -2] = sqrt(0.5).
    rows = [day_trade("2026-10-07", 4.0), day_trade("2026-10-08", -1.0),
            day_trade("2026-10-09", -2.0)]
    s = compute_stats(rows)
    assert s["sortino_days"] == 3
    assert s["sortino_ratio"] == expected_sortino([4.0, -1.0, -2.0])
    assert f"  Sortino ratio (ann):{s['sortino_ratio']:.2f}" in format_report(s)


def test_sortino_ratio_ignores_upside():
    # Same upside series as test 1 but a much bigger up day: the ratio must
    # move, but never because the upside widened the denominator.
    rows = [day_trade("2026-10-07", 40.0), day_trade("2026-10-08", -1.0),
            day_trade("2026-10-09", -2.0)]
    s = compute_stats(rows)
    assert s["sortino_ratio"] == expected_sortino([40.0, -1.0, -2.0])
    # Sharpe would have been crushed by the wider total variance here.
    assert s["sharpe_ratio"] is not None
    assert s["sortino_ratio"] != s["sharpe_ratio"]


def test_sortino_ratio_none_no_losing_days():
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0)]
    s = compute_stats(rows)
    assert s["sortino_days"] == 2
    assert s["sortino_ratio"] is None
    assert "  Sortino ratio (ann):n/a" in format_report(s)


def test_sortino_ratio_none_single_losing_day():
    rows = [day_trade("2026-10-07", 5.0), day_trade("2026-10-08", -1.0),
            day_trade("2026-10-09", 3.0)]
    s = compute_stats(rows)
    assert s["sortino_ratio"] is None


def test_sortino_ratio_none_zero_downside_variance():
    # Two identical losing days: denominator stdev is 0.
    rows = [day_trade("2026-10-07", 5.0), day_trade("2026-10-08", -2.0),
            day_trade("2026-10-09", -2.0)]
    s = compute_stats(rows)
    assert s["sortino_ratio"] is None
    assert s["sharpe_ratio"] is not None


def test_sortino_ratio_none_single_day():
    s = compute_stats([day_trade("2026-10-09", -10.0)])
    assert s["sortino_days"] == 1
    assert s["sortino_ratio"] is None


def test_sortino_ratio_excludes_undated():
    rows = [
        day_trade("2026-10-07", 4.0),
        day_trade("2026-10-08", -1.0),
        day_trade("2026-10-09", -2.0),
        {**day_trade("2026-10-09", -100.0), "date": None},  # undated: ignored
    ]
    s = compute_stats(rows)
    assert s["sortino_days"] == 3
    assert s["sortino_ratio"] == expected_sortino([4.0, -1.0, -2.0])


def test_sortino_ratio_json_and_csv_carry():
    rows = [day_trade("2026-10-07", 4.0), day_trade("2026-10-08", -1.0),
            day_trade("2026-10-09", -2.0)]
    s = compute_stats(rows)
    payload = json.loads(format_json(s))
    assert payload["stats"]["sortino_ratio"] == s["sortino_ratio"]
    assert payload["stats"]["sortino_days"] == 3
    lines = format_csv(s).strip().splitlines()
    risk = [ln for ln in lines if ln.startswith("risk,")]
    assert risk == [f"risk,sharpe_ratio_ann,3,,,{s['sharpe_ratio']:.2f}",
                    f"risk,sortino_ratio_ann,3,,,{s['sortino_ratio']:.2f}"]


def test_sortino_ratio_csv_omitted_when_undefined():
    rows = [day_trade("2026-10-07", 1.0), day_trade("2026-10-08", 2.0)]
    s = compute_stats(rows)
    lines = format_csv(s).strip().splitlines()
    assert not [ln for ln in lines if ln.startswith("risk,sortino")]
