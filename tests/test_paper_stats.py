"""Unit tests for paper_stats — read-only journal crunching, fixture-driven."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import (
    APPEND_JOURNAL_FILENAME,
    compute_stats,
    default_append_journal_path,
    format_report,
    load_append_journal,
    load_journal,
    merge_trades,
)


def trade(time, symbol, side, qty, price, pnl, paper=True):
    return {
        "date": "2026-10-09", "time": time, "symbol": symbol, "side": side,
        "quantity": qty, "price": price, "order_id": f"paper-{time}",
        "pnl": pnl, "paper": paper,
    }


# AAPL round trip: +10 then -5 (net +5, AAPL flat).
# MSFT buy 2 @ 400: still open (pnl 0). NVDA: live trade, excluded from paper stats.
FIXTURE = [
    trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
    trade("2026-10-09T09:45:00", "AAPL", "sell", 1, 210.0, -5.0),
    trade("2026-10-09T10:00:00", "MSFT", "buy", 2, 400.0, 0.0),
]
FIXTURE_WITH_LIVE = FIXTURE + [
    trade("2026-10-09T10:15:00", "NVDA", "buy", 1, 150.0, 3.0, paper=False),
]


def paper_only(trades):
    return [t for t in trades if t.get("paper")]


def test_win_rate_and_averages():
    s = compute_stats(FIXTURE)
    assert s["trades"] == 3
    assert s["realized_trades"] == 2
    assert (s["wins"], s["losses"]) == (1, 1)
    assert s["win_rate"] == 0.5
    assert s["avg_win"] == 10.0
    assert s["avg_loss"] == -5.0
    assert s["profit_factor"] == 2.0
    assert s["total_pnl"] == 5.0


def test_drawdown_and_exposure():
    s = compute_stats(FIXTURE)
    # Cumulative P&L: 10, 5, 5 -> peak 10, trough 5 -> drawdown 5
    assert s["max_drawdown"] == 5.0
    # AAPL flat, MSFT long 2 @ 400 open
    assert s["open_exposure"] == 800.0
    assert s["positions"] == {"MSFT": 800.0}
    assert s["gross_notional"] == 200.0 + 210.0 + 800.0


def test_live_trades_excluded_from_paper_stats():
    s = compute_stats(paper_only(FIXTURE_WITH_LIVE))
    assert s["trades"] == 3
    assert s["total_pnl"] == 5.0
    s_all = compute_stats(FIXTURE_WITH_LIVE)
    assert s_all["trades"] == 4
    assert s_all["total_pnl"] == 8.0


def test_empty_journal():
    s = compute_stats([])
    assert s["trades"] == 0
    assert s["win_rate"] is None
    assert s["avg_win"] is None
    assert s["avg_loss"] is None
    assert s["profit_factor"] is None
    assert s["total_pnl"] == 0.0
    assert s["max_drawdown"] == 0.0
    assert s["open_exposure"] == 0.0
    assert s["positions"] == {}


def test_no_realized_pnl_yet():
    s = compute_stats([trade("2026-10-09T10:00:00", "MSFT", "buy", 2, 400.0, 0.0)])
    assert s["win_rate"] is None
    assert s["total_pnl"] == 0.0
    assert s["open_exposure"] == 800.0


def test_all_winners_no_profit_factor_denominator():
    s = compute_stats([
        trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
        trade("2026-10-09T09:45:00", "AAPL", "sell", 1, 210.0, 4.0),
    ])
    assert s["win_rate"] == 1.0
    assert s["profit_factor"] is None  # no losses -> undefined
    assert s["max_drawdown"] == 0.0


def test_load_journal_missing_and_corrupt():
    assert load_journal(os.path.join(tempfile.mkdtemp(), "nope.json")) == []
    bad = os.path.join(tempfile.mkdtemp(), "bad.json")
    with open(bad, "w") as f:
        f.write("{not json")
    assert load_journal(bad) == []


def test_load_journal_roundtrip():
    d = tempfile.mkdtemp()
    path = os.path.join(d, ".agent_state.json")
    with open(path, "w") as f:
        json.dump({"date": "2026-10-09", "trades": FIXTURE}, f)
    assert load_journal(path) == FIXTURE


def test_format_report_shows_key_lines():
    out = format_report(compute_stats(FIXTURE))
    for needle in ("Win rate:           50.0%", "Avg win:            $10.00",
                   "Avg loss:           $-5.00", "Total P&L:          $5.00",
                   "Max drawdown:       $5.00", "Open exposure:      $800.00",
                   "MSFT: $800.00"):
        assert needle in out, needle
    out_empty = format_report(compute_stats([]))
    assert "Win rate:           n/a" in out_empty
    assert "Open positions:     none" in out_empty


def test_default_append_journal_path_name():
    assert default_append_journal_path().endswith(APPEND_JOURNAL_FILENAME)
    assert APPEND_JOURNAL_FILENAME == "paper_journal.jsonl"


def test_load_append_journal_roundtrip_and_skips_bad_lines():
    d = tempfile.mkdtemp()
    path = os.path.join(d, APPEND_JOURNAL_FILENAME)
    with open(path, "w") as f:
        f.write(json.dumps(FIXTURE[0]) + "\n")
        f.write("{not json\n")          # corrupt line skipped
        f.write("\n")                   # blank line skipped
        f.write("[1, 2]\n")             # non-dict line skipped
        f.write(json.dumps(FIXTURE[1]) + "\n")
    assert load_append_journal(path) == [FIXTURE[0], FIXTURE[1]]


def test_load_append_journal_missing_returns_empty():
    assert load_append_journal(os.path.join(tempfile.mkdtemp(), "nope.jsonl")) == []


def test_merge_trades_dedupes_journal_and_state_overlap():
    # The same trade recorded in the journal and still sitting in today's
    # state file must only count once in stats.
    merged = merge_trades([FIXTURE[0], FIXTURE[1]], [FIXTURE[1], FIXTURE[2]])
    assert merged == [FIXTURE[0], FIXTURE[1], FIXTURE[2]]
    s = compute_stats(merged)
    assert s["trades"] == 3
    assert s["total_pnl"] == 5.0


def test_merge_trades_empty():
    assert merge_trades() == []
    assert merge_trades([], []) == []
