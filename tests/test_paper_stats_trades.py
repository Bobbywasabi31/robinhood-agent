"""`stats --trades` per-trade CSV export tests for paper_stats / agent.py.

One CSV row per paper trade (date,time,symbol,side,quantity,price,
realized_pnl) so the raw journal pipes straight into spreadsheets or
pandas; composes with --from/--to, --days and --symbols, and refuses
--json/--csv company (exit 2). Read-only — no trading logic involved.
"""
import csv
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import format_trades_csv, prepare_trades, compute_stats


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
    rows = parse(format_trades_csv([]))
    assert rows == [["date", "time", "symbol", "side", "quantity", "price",
                     "realized_pnl"]]


def test_rows_follow_date_then_time_order():
    trades = [
        trade("2026-10-09", "2026-10-09T10:15:00", "NVDA", pnl=6.0),
        trade("2026-10-06", "2026-10-06T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-07", "2026-10-07T09:45:00", "AAPL", "sell", pnl=-4.0),
        # Same day, later time first in input: must come out in time order.
        trade("2026-10-06", "2026-10-06T15:59:00", "MSFT", pnl=0.0),
    ]
    rows = parse(format_trades_csv(prepare_trades(trades)[0]))
    assert [r[0] for r in rows[1:]] == ["2026-10-06", "2026-10-06",
                                        "2026-10-07", "2026-10-09"]
    assert rows[1][5] == "100.00"  # 09:31 AAPL first
    assert rows[2][2] == "MSFT"    # 15:59 MSFT second


def test_money_is_plain_decimal():
    rows = parse(format_trades_csv(
        [trade("2026-10-09", "2026-10-09T10:15:00", "AAPL",
              qty=2, price=12345.678, pnl=-1000.5)]))
    assert rows[1][5] == "12345.68"   # no $ or thousands separators
    assert rows[1][6] == "-1000.50"
    assert rows[1][4] == "2"


def test_missing_fields_are_empty_cells():
    rows = parse(format_trades_csv([{"date": "2026-10-09", "paper": True}]))
    assert rows[1] == ["2026-10-09", "", "", "", "", "", ""]
    # A symbol-less trade stays blank here (the aggregate sections
    # use an UNKNOWN fallback — raw listings don't invent data).


def test_symbol_is_uppercased():
    rows = parse(format_trades_csv(
        [trade("2026-10-09", "2026-10-09T10:15:00", "aapl")]))
    assert rows[1][2] == "AAPL"


def test_prepare_trades_shares_compute_stats_filters():
    trades = [
        trade("2026-10-06", "2026-10-06T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-07", "2026-10-07T09:45:00", "MSFT", pnl=-4.0),
        trade("2026-10-08", "2026-10-08T10:00:00", "AAPL", pnl=6.0),
    ]
    kept, undated, sym_excl = prepare_trades(
        trades, date_from="2026-10-07", symbols="AAPL")
    assert [t["date"] for t in kept] == ["2026-10-08"]
    assert undated == 0
    assert sym_excl == 1  # 2026-10-07 MSFT in range, filtered by symbol
    # Same trade list feeds compute_stats identically.
    s = compute_stats(trades, date_from="2026-10-07", symbols="AAPL")
    assert s["trades"] == 1
    assert s["total_pnl"] == 6.0
    assert s["symbol_excluded"] == 1


def test_prepare_trades_reports_undated_excluded():
    trades = [trade(None, "2026-10-09T10:15:00", "AAPL", pnl=5.0),
              trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=3.0)]
    kept, undated, _ = prepare_trades(
        trades, date_from="2026-10-01", date_to="2026-10-31")
    assert [t["symbol"] for t in kept] == ["MSFT"]
    assert undated == 1


def test_cmd_stats_trades_end_to_end():
    """agent.py stats --trades: parseable per-trade CSV, live trades out."""
    import agent
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    rows = [
        trade("2026-10-09", "2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", "sell", 1, 400.0, -4.0),
        trade("2026-10-09", "2026-10-09T11:00:00", "NVDA", "buy", 1, 150.0,
              6.0, paper=False),  # live: excluded with a stderr note
    ]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in rows:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_trades=True)
        out = buf.getvalue()
    parsed = parse(out)
    assert parsed[0] == ["date", "time", "symbol", "side", "quantity",
                         "price", "realized_pnl"]
    assert len(parsed) == 3  # header + 2 paper trades, live one dropped
    assert parsed[1][2] == "AAPL" and parsed[1][6] == "10.00"
    assert parsed[2][2] == "MSFT" and parsed[2][3] == "sell"
    assert "Paper journal stats" not in out  # no text-report bleed


def test_cmd_stats_trades_composes_with_filters():
    import agent
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    rows = [
        trade("2026-10-08", "2026-10-08T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=-4.0),
        trade("2026-10-10", "2026-10-10T11:00:00", "AAPL", pnl=6.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in rows:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_trades=True,
                            date_from="2026-10-09", date_to="2026-10-10",
                            symbols="AAPL")
        parsed = parse(buf.getvalue())
    assert len(parsed) == 2  # header + the one AAPL trade in range
    assert parsed[1][0] == "2026-10-10"


def test_cmd_stats_trades_rejects_json_company():
    import agent
    from contextlib import redirect_stderr
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                agent.cmd_stats(guards, as_trades=True, as_json=True)
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("expected SystemExit(2)")
    assert "only one of" in err.getvalue()


def test_cmd_stats_trades_warns_on_top_n():
    import agent
    from contextlib import redirect_stdout, redirect_stderr
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            agent.cmd_stats(guards, as_trades=True, top_n=5)
        assert parse(out.getvalue())  # still prints the header
    assert "--top is ignored with --trades" in err.getvalue()
