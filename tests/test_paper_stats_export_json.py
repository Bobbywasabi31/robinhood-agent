"""`stats --trades --json` / `stats --equity --json` machine-readable exports.

--json acts as a format modifier on the two CSV export modes (it stays
mutually exclusive with --csv). Money is JSON numbers, missing fields
are null, exclusion counts ride in the payload like format_json().
Read-only — no trading logic involved.
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paper_stats import format_trades_json, format_equity_json


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


def test_trades_json_payload_shape():
    rows = [trade("2026-10-09", "2026-10-09T09:31:00", "aapl", "buy", 2,
                  200.5, 10.25)]
    payload = json.loads(format_trades_json(rows, live_excluded=3))
    assert payload["live_excluded"] == 3
    assert payload["trades"] == [{
        "date": "2026-10-09", "time": "2026-10-09T09:31:00",
        "symbol": "AAPL", "side": "buy", "quantity": 2,
        "price": 200.5, "realized_pnl": 10.25,
    }]


def test_trades_json_missing_fields_are_null():
    payload = json.loads(format_trades_json(
        [{"date": "2026-10-09", "symbol": "msft"}]))
    row = payload["trades"][0]
    assert row["time"] is None
    assert row["symbol"] == "MSFT"  # uppercased even when sparse
    assert row["side"] is None
    assert row["quantity"] is None
    assert row["price"] is None
    assert row["realized_pnl"] is None
    assert payload["live_excluded"] == 0


def test_trades_json_empty_list():
    payload = json.loads(format_trades_json([]))
    assert payload == {"trades": [], "live_excluded": 0}


def test_equity_json_payload_shape():
    rows = [
        trade("2026-10-09", "2026-10-09T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=-4.0),
        trade("2026-10-10", "2026-10-10T09:31:00", "AAPL", pnl=6.0),
    ]
    payload = json.loads(format_equity_json(rows, live_excluded=1))
    assert payload["equity"] == [
        {"date": "2026-10-09", "daily_pnl": 6.0, "cumulative_pnl": 6.0},
        {"date": "2026-10-10", "daily_pnl": 6.0, "cumulative_pnl": 12.0},
    ]
    assert payload["undated_excluded"] == 0
    assert payload["live_excluded"] == 1


def test_equity_json_undated_counted():
    rows = [
        trade("2026-10-09", "2026-10-09T09:31:00", "AAPL", pnl=10.0),
        trade(None, "2026-10-09T10:15:00", "MSFT", pnl=-4.0),
    ]
    payload = json.loads(format_equity_json(rows))
    assert len(payload["equity"]) == 1  # undated row can't join the series
    assert payload["undated_excluded"] == 1
    assert payload["equity"][0]["cumulative_pnl"] == 10.0


def test_equity_json_empty():
    payload = json.loads(format_equity_json([]))
    assert payload == {"equity": [], "undated_excluded": 0,
                       "live_excluded": 0}


def _journal(guards_dir, rows):
    with open(os.path.join(guards_dir, "paper_journal.jsonl"), "w") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")


def test_cmd_stats_equity_json_end_to_end():
    import agent
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    rows = [
        trade("2026-10-09", "2026-10-09T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-10", "2026-10-10T09:31:00", "MSFT", pnl=-4.0),
        trade("2026-10-10", "2026-10-10T11:00:00", "NVDA", pnl=1.0,
              paper=False),  # live: counted, not listed
    ]
    with tempfile.TemporaryDirectory() as tmp:
        _journal(tmp, rows)
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_equity=True, as_json=True)
        payload = json.loads(buf.getvalue())
    assert payload["live_excluded"] == 1
    assert payload["undated_excluded"] == 0
    assert payload["equity"] == [
        {"date": "2026-10-09", "daily_pnl": 10.0, "cumulative_pnl": 10.0},
        {"date": "2026-10-10", "daily_pnl": -4.0, "cumulative_pnl": 6.0},
    ]


def test_cmd_stats_equity_json_composes_with_filters():
    import agent
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    rows = [
        trade("2026-10-08", "2026-10-08T09:31:00", "AAPL", pnl=10.0),
        trade("2026-10-09", "2026-10-09T10:15:00", "MSFT", pnl=-4.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        _journal(tmp, rows)
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_equity=True, as_json=True,
                            date_from="2026-10-09")
        payload = json.loads(buf.getvalue())
    assert [r["date"] for r in payload["equity"]] == ["2026-10-09"]
    assert payload["equity"][0]["cumulative_pnl"] == -4.0


def test_cmd_stats_json_still_rejects_csv():
    import agent
    from contextlib import redirect_stderr
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                agent.cmd_stats(guards, as_csv=True, as_json=True)
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("expected SystemExit(2)")
    assert "--json can't combine with --csv" in err.getvalue()
