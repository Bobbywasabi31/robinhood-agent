"""`stats --top N` per-symbol leaderboard tests for paper_stats.

--top N keeps the N best and N worst tickers by realized P&L in the
per-symbol section (text + CSV become a best-to-worst leaderboard); the
rest are counted as by_symbol_omitted and --json carries the fields.
A read-only display filter — no trading logic involved.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import (
    _symbol_order,
    compute_stats,
    format_csv,
    format_json,
    format_report,
    resolve_top_n,
)


def trade(date, pnl, symbol="AAPL"):
    return {
        "date": date, "time": "09:31:00", "symbol": symbol, "side": "buy",
        "quantity": 1, "price": 100.0,
        "order_id": f"paper-{date}-{symbol}-{pnl}",
        "pnl": pnl, "paper": True,
    }


def six_symbols():
    # AAA +30, BBB +20, CCC +10, DDD -10, EEE -20, FFF -30
    return [
        trade("2026-10-01", 30.0, "AAA"), trade("2026-10-01", 20.0, "BBB"),
        trade("2026-10-01", 10.0, "CCC"), trade("2026-10-01", -10.0, "DDD"),
        trade("2026-10-01", -20.0, "EEE"), trade("2026-10-01", -30.0, "FFF"),
    ]


def test_top_n_keeps_gainers_and_losers():
    s = compute_stats(six_symbols(), top_n=2)
    assert set(s["by_symbol"]) == {"AAA", "BBB", "EEE", "FFF"}
    assert s["top_n"] == 2
    assert s["by_symbol_omitted"] == 2


def test_top_n_overlap_dedupes():
    rows = [trade("2026-10-01", 10.0, "AAA"),
            trade("2026-10-01", -10.0, "BBB"),
            trade("2026-10-01", 0.0, "CCC")]
    s = compute_stats(rows, top_n=2)
    # Top 2 gainers = AAA, CCC; top 2 losers = BBB, CCC: CCC in both halves.
    assert set(s["by_symbol"]) == {"AAA", "BBB", "CCC"}
    assert s["by_symbol_omitted"] == 0


def test_top_n_tie_breaks_on_symbol_name():
    rows = [trade("2026-10-01", 10.0, "Y"),
            trade("2026-10-01", 10.0, "X"),
            trade("2026-10-01", -5.0, "Z")]
    s = compute_stats(rows, top_n=1)
    # Gainers tie at +10: X wins the single slot on the name tie-break.
    assert set(s["by_symbol"]) == {"X", "Z"}
    assert s["by_symbol_omitted"] == 1


def test_top_n_larger_than_universe_is_passthrough():
    s = compute_stats(six_symbols(), top_n=10)
    assert len(s["by_symbol"]) == 6
    assert s["by_symbol_omitted"] == 0
    assert s["top_n"] == 10


def test_top_n_none_keeps_everything():
    s = compute_stats(six_symbols())
    assert s["top_n"] is None
    assert s["by_symbol_omitted"] == 0
    assert len(s["by_symbol"]) == 6


def test_leaderboard_order_best_to_worst():
    s = compute_stats(six_symbols(), top_n=2)
    assert _symbol_order(s) == ["AAA", "BBB", "EEE", "FFF"]


def test_plain_order_stays_alphabetical():
    s = compute_stats(six_symbols())
    assert _symbol_order(s) == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def test_report_header_and_omission_note():
    s = compute_stats(six_symbols(), top_n=2)
    report = format_report(s)
    assert "Per-symbol P&L (top 2 + bottom 2 by realized P&L):" in report
    assert "... 2 symbol(s) between top/bottom 2 not shown" in report
    # Leaderboard order in the report: AAA before EEE before FFF.
    assert (report.index("AAA:") < report.index("EEE:")
            < report.index("FFF:"))
    # DDD is cut from the per-symbol section (it still appears under
    # Open positions, which --top does not filter).
    section = report.split("Per-symbol P&L")[1].split("Open positions:")[0]
    assert "DDD:" not in section


def test_report_plain_header_when_no_top():
    s = compute_stats(six_symbols())
    report = format_report(s)
    assert "  Per-symbol P&L:\n" in report
    assert "between top/bottom" not in report


def test_csv_symbol_rows_follow_leaderboard_order():
    s = compute_stats(six_symbols(), top_n=2)
    rows = [ln for ln in format_csv(s).splitlines() if ln.startswith("symbol,")]
    assert [r.split(",")[1] for r in rows] == ["AAA", "BBB", "EEE", "FFF"]


def test_json_carries_top_n_fields():
    s = compute_stats(six_symbols(), top_n=2)
    payload = json.loads(format_json(s))
    assert payload["stats"]["top_n"] == 2
    assert payload["stats"]["by_symbol_omitted"] == 2
    assert set(payload["stats"]["by_symbol"]) == {"AAA", "BBB", "EEE", "FFF"}


def test_top_n_applies_after_symbols_filter():
    s = compute_stats(six_symbols(), symbols="AAA,BBB,CCC,DDD", top_n=1)
    # Of the 4-symbol universe, AAA is the best and DDD the worst.
    assert set(s["by_symbol"]) == {"AAA", "DDD"}
    assert s["by_symbol_omitted"] == 2


def test_resolve_top_n_accepts_positive_ints():
    assert resolve_top_n("5") == 5
    assert resolve_top_n(3) == 3
    assert resolve_top_n(" 7 ") == 7


def test_resolve_top_n_rejects_garbage():
    for bad in ("0", "-2", "abc", "2.5", "", None):
        try:
            resolve_top_n(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"resolve_top_n({bad!r}) did not raise")


def test_compute_stats_rejects_bad_top_n():
    for bad in (0, -1, "3"):
        try:
            compute_stats(six_symbols(), top_n=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"compute_stats(top_n={bad!r}) did not raise")
