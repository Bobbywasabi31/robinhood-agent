"""`stats --equity` daily equity-curve CSV export tests for paper_stats / agent.py.

One row per dated trading day, sorted chronologically: date, the day's
realized P&L, and the running cumulative P&L — the same daily series
behind the Sharpe/Sortino/Calmar ratios, plottable straight out of the
box. Composes with --from/--to, --days and --symbols; --equity --json
renders the same series as machine-readable JSON; refuses --csv/--trades
company (exit 2). Read-only — no trading logic.
"""
import csv
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import format_equity_csv, prepare_trades


def trade(date, time, symbol, side="buy", qty=1, price=100.0, pnl=0.0,
          paper=True, **kw):
    t = {
        "date": date, "time": time, "symbol": symbol, "side": side,
        "quantity": qty, "price": price,
        "order_id": f"paper-{date}-{time}-{symbol}-{pnl}",
        "pnl": pnl, "paper": paper,
    }
    t.update(kw)
    return t


def parse(csv_text):
    return list(csv.reader(io.StringIO(csv_text)))


def test_empty_list_yields_header_only():
    text, undated = format_equity_csv([])
    assert parse(text) == [["date", "daily_pnl", "cumulative_pnl"]]
    assert undated == 0


def test_curve_math_and_chronological_order():
    trades = [
        # Out-of-order input on purpose; same-day trades aggregate.
        trade("2026-10-09", "2026-10-09T10:15:00", "NVDA", pnl=5.0),
        trade("2026-10-06", "2026-10-06T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-07", "2026-10-07T09:45:00", "AAPL", "sell", pnl=-20.0),
        trade("2026-10-06", "2026-10-06T15:59:00", "MSFT", pnl=-4.0),
    ]
    text, undated = format_equity_csv(trades)
    rows = parse(text)
    assert rows[1:] == [
        ["2026-10-06", "6.00", "6.00"],
        ["2026-10-07", "-20.00", "-14.00"],
        ["2026-10-09", "5.00", "-9.00"],
    ]
    assert undated == 0


def test_undated_trades_skipped_and_counted():
    trades = [
        trade(None, "2026-10-09T10:15:00", "AAPL", pnl=100.0),
        trade("not-a-date", "2026-10-09T10:15:00", "AAPL", pnl=200.0),
        {"time": "2026-10-09T10:15:00", "paper": True, "pnl": 300.0},
        trade("2026-10-09", "2026-10-09T11:00:00", "MSFT", pnl=4.0),
    ]
    text, undated = format_equity_csv(trades)
    assert parse(text)[1:] == [["2026-10-09", "4.00", "4.00"]]
    assert undated == 3


def test_money_is_plain_decimal():
    # 0.1 + 0.2 must round to 0.30, never print as 0.30000000000000004.
    text, _ = format_equity_csv([
        trade("2026-10-09", "2026-10-09T09:31:00", "AAPL", pnl=0.1),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=0.2),
    ])
    assert parse(text)[1] == ["2026-10-09", "0.30", "0.30"]


def test_composes_with_prepare_trades_filters():
    trades = [
        trade("2026-10-06", "2026-10-06T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-07", "2026-10-07T09:45:00", "MSFT", pnl=-4.0),
        trade("2026-10-08", "2026-10-08T10:00:00", "AAPL", pnl=6.0),
    ]
    kept, _, _ = prepare_trades(
        trades, date_from="2026-10-07", symbols="AAPL")
    text, undated = format_equity_csv(kept)
    assert parse(text)[1:] == [["2026-10-08", "6.00", "6.00"]]
    assert undated == 0


def test_cmd_stats_equity_end_to_end():
    """agent.py stats --equity: pure-CSV equity curve, live trades out."""
    import agent
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    rows = [
        trade("2026-10-08", "2026-10-08T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", "sell", 1, 400.0, -4.0),
        trade("2026-10-09", "2026-10-09T11:00:00", "NVDA", "buy", 1, 150.0,
              600.0, paper=False),  # live: excluded with a stderr note
    ]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in rows:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_equity=True)
        out = buf.getvalue()
    parsed = parse(out)
    assert parsed[0] == ["date", "daily_pnl", "cumulative_pnl"]
    assert parsed[1:] == [
        ["2026-10-08", "10.00", "10.00"],
        ["2026-10-09", "-4.00", "6.00"],  # the live +600 is out
    ]
    assert "Per-symbol P&L" not in out  # no text-report bleed


def test_cmd_stats_equity_warns_on_undated():
    """Trades that can't join the time series are noted on stderr."""
    import agent
    from contextlib import redirect_stdout, redirect_stderr
    from types import SimpleNamespace

    rows = [
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=-4.0),
        trade(None, "2026-10-09T11:00:00", "AAPL", pnl=50.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in rows:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            agent.cmd_stats(guards, as_equity=True)
        parsed = parse(out.getvalue())
    assert len(parsed) == 2  # header + the one dated day
    assert parsed[1] == ["2026-10-09", "-4.00", "-4.00"]
    assert "without a valid date" in err.getvalue()


def test_cmd_stats_equity_rejects_csv_company():
    import agent
    from contextlib import redirect_stderr
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                agent.cmd_stats(guards, as_equity=True, as_csv=True)
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("expected SystemExit(2)")
    assert "only one of" in err.getvalue()


def test_cmd_stats_equity_warns_on_top_n():
    import agent
    from contextlib import redirect_stdout, redirect_stderr
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            agent.cmd_stats(guards, as_equity=True, top_n=5)
        assert parse(out.getvalue())  # still prints the header
    assert "--top is ignored with --equity" in err.getvalue()
