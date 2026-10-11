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
    filter_trades_by_date,
    filter_trades_by_symbols,
    format_csv,
    format_json,
    format_report,
    load_append_journal,
    load_journal,
    merge_trades,
    normalize_symbols,
    parse_iso_date,
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


def test_by_symbol_breakdown():
    s = compute_stats(FIXTURE)
    by = s["by_symbol"]
    # AAPL round trip: +10 then -5 -> net +5, 1W/1L.
    assert by["AAPL"] == {"trades": 2, "realized": 2, "wins": 1, "losses": 1, "pnl": 5.0}
    # MSFT still open: pnl 0, no realized trades.
    assert by["MSFT"] == {"trades": 1, "realized": 0, "wins": 0, "losses": 0, "pnl": 0.0}


def test_by_symbol_empty_trades():
    assert compute_stats([])["by_symbol"] == {}


def test_format_report_includes_per_symbol():
    s = compute_stats(FIXTURE)
    report = format_report(s)
    assert "Per-symbol P&L:" in report
    assert "AAPL: $5.00 over 2 trades (1W/1L)" in report
    assert "MSFT: $0.00 over 1 trades (0W/0L)" in report


def test_by_symbol_unknown_symbol_fallback():
    t = trade("2026-10-09T11:00:00", "", "buy", 1, 100.0, 7.0)
    s = compute_stats([t])
    assert s["by_symbol"]["UNKNOWN"]["pnl"] == 7.0


def trade_on(date, time, symbol, side, qty, price, pnl):
    t = trade(time, symbol, side, qty, price, pnl)
    t["date"] = date
    t["order_id"] = f"paper-{date}-{time}"
    return t


def test_multiday_journal_orders_by_date_then_time():
    # A morning trade on day 2 must NOT sort before an afternoon trade on day 1,
    # or the cumulative curve (and drawdown) would be wrong.
    trades = [
        trade_on("2026-10-09", "2026-10-09T14:00:00", "AAPL", "buy", 1, 200.0, -5.0),
        trade_on("2026-10-10", "2026-10-10T09:31:00", "AAPL", "sell", 1, 210.0, 10.0),
    ]
    s = compute_stats(trades)
    # Cumulative: -5, 5 -> peak 0... actually peak 0, trough -5, then 5.
    # peak tracks max cumulative: 0 then 5. max_dd = 0 - (-5) = 5.
    assert s["max_drawdown"] == 5.0
    assert s["total_pnl"] == 5.0


def test_multiday_drawdown_uncorrected_sort_would_fail():
    # With the old time-only sort, 09:31 < 14:00 would invert the days and
    # hide this drawdown. This trade ordering must reflect calendar order.
    trades = [
        trade_on("2026-10-08", "2026-10-08T15:00:00", "MSFT", "buy", 1, 400.0, 20.0),
        trade_on("2026-10-09", "2026-10-09T10:00:00", "MSFT", "sell", 1, 420.0, -12.0),
        trade_on("2026-10-09", "2026-10-09T11:00:00", "MSFT", "buy", 1, 408.0, 3.0),
    ]
    s = compute_stats(trades)
    # Cumulative: 20, 8, 11 -> peak 20, trough 8 -> drawdown 12.
    assert s["max_drawdown"] == 12.0


def test_by_day_groups_pnl_per_calendar_day():
    trades = [
        trade_on("2026-10-09", "2026-10-09T14:00:00", "AAPL", "buy", 1, 200.0, -5.0),
        trade_on("2026-10-09", "2026-10-09T15:00:00", "AAPL", "sell", 1, 210.0, 8.0),
        trade_on("2026-10-10", "2026-10-10T09:31:00", "AAPL", "buy", 1, 205.0, 4.0),
    ]
    s = compute_stats(trades)
    by = s["by_day"]
    assert by["2026-10-09"] == {"trades": 2, "pnl": 3.0, "wins": 1, "losses": 1}
    assert by["2026-10-10"] == {"trades": 1, "pnl": 4.0, "wins": 1, "losses": 0}


def test_by_day_empty_and_undated():
    assert compute_stats([])["by_day"] == {}
    t = trade("2026-10-09T10:00:00", "MSFT", "buy", 1, 400.0, 7.0)
    del t["date"]
    s = compute_stats([t])
    assert s["by_day"]["undated"] == {"trades": 1, "pnl": 7.0, "wins": 1, "losses": 0}


def test_format_report_includes_daily_pnl():
    trades = [
        trade_on("2026-10-09", "2026-10-09T14:00:00", "AAPL", "buy", 1, 200.0, -5.0),
        trade_on("2026-10-10", "2026-10-10T09:31:00", "AAPL", "buy", 1, 205.0, 4.0),
    ]
    report = format_report(compute_stats(trades))
    assert "Daily P&L:" in report
    assert "2026-10-09: $-5.00 over 1 trades (0W/1L)" in report
    assert "2026-10-10: $4.00 over 1 trades (1W/0L)" in report


def test_format_json_roundtrips_compute_stats():
    payload = json.loads(format_json(compute_stats(FIXTURE)))
    assert payload["live_excluded"] == 0
    s = payload["stats"]
    assert s["trades"] == 3
    assert s["total_pnl"] == 5.0
    assert s["win_rate"] == 0.5
    assert s["by_day"]["2026-10-09"]["pnl"] == 5.0
    assert s["by_symbol"]["AAPL"]["pnl"] == 5.0


def test_format_json_carries_live_excluded():
    payload = json.loads(
        format_json(compute_stats(paper_only(FIXTURE_WITH_LIVE)), live_excluded=1))
    assert payload["live_excluded"] == 1
    assert payload["stats"]["trades"] == 3  # live trade dropped before crunching


def test_format_json_empty_stats_is_valid():
    payload = json.loads(format_json(compute_stats([])))
    assert payload["stats"]["trades"] == 0
    assert payload["stats"]["win_rate"] is None  # None -> null, not an error
    assert payload["live_excluded"] == 0


def test_cmd_stats_json_end_to_end():
    """agent.py stats --json prints one parseable JSON object, not the text report."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in FIXTURE_WITH_LIVE:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_json=True)
        out = buf.getvalue()
    payload = json.loads(out)
    assert payload["live_excluded"] == 1
    assert payload["stats"]["trades"] == 3
    assert payload["stats"]["total_pnl"] == 5.0
    assert "Paper journal stats" not in out  # no text-report bleed


def test_cmd_stats_text_default_unchanged():
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards)
        out = buf.getvalue()
    assert "Paper journal stats" in out
    assert "No paper trades in the journal yet." in out


MULTI_DAY = [
    trade_on("2026-10-06", "2026-10-06T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
    trade_on("2026-10-07", "2026-10-07T09:45:00", "AAPL", "sell", 1, 210.0, -4.0),
    trade_on("2026-10-08", "2026-10-08T10:00:00", "MSFT", "buy", 2, 400.0, 0.0),
    trade_on("2026-10-09", "2026-10-09T10:15:00", "NVDA", "buy", 1, 150.0, 6.0),
]


def test_parse_iso_date():
    assert str(parse_iso_date("2026-10-09")) == "2026-10-09"
    assert parse_iso_date("  2026-10-09  ") is not None  # padded ok
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None
    for bad in ("10/09/2026", "2026-13-01", "yesterday", "2026-10-9x"):
        try:
            parse_iso_date(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_filter_trades_by_date_bounds():
    kept, und = filter_trades_by_date(MULTI_DAY, "2026-10-07", "2026-10-08")
    assert und == 0
    assert [t["date"] for t in kept] == ["2026-10-07", "2026-10-08"]  # inclusive
    kept, _ = filter_trades_by_date(MULTI_DAY, date_from="2026-10-08")
    assert [t["date"] for t in kept] == ["2026-10-08", "2026-10-09"]
    kept, _ = filter_trades_by_date(MULTI_DAY, date_to="2026-10-06")
    assert [t["date"] for t in kept] == ["2026-10-06"]


def test_filter_trades_by_date_no_bounds_is_passthrough():
    kept, und = filter_trades_by_date(MULTI_DAY)
    assert len(kept) == len(MULTI_DAY) and und == 0


def test_filter_trades_by_date_undated():
    rows = MULTI_DAY + [
        {**trade("09:31:00", "X", "buy", 1, 1.0, 0.0), "date": ""},
        {**trade("09:32:00", "Y", "buy", 1, 1.0, 0.0), "date": "not-a-date"},
    ]
    kept, und = filter_trades_by_date(rows, "2026-10-06", "2026-10-09")
    assert und == 2  # blank + garbage dates dropped while filtering
    assert all(t.get("symbol") not in ("X", "Y") for t in kept)
    kept, und = filter_trades_by_date(rows)  # no bounds: everything kept
    assert und == 0 and len(kept) == len(rows)


def test_filter_trades_by_date_bad_bounds():
    for kwargs in ({"date_from": "10/09/2026"},
                   {"date_to": "next friday"},
                   {"date_from": "2026-10-09", "date_to": "2026-10-06"}):
        try:
            filter_trades_by_date(MULTI_DAY, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_compute_stats_records_date_range():
    stats = compute_stats(MULTI_DAY, date_from="2026-10-07", date_to="2026-10-08")
    assert stats["date_from"] == "2026-10-07"
    assert stats["date_to"] == "2026-10-08"
    assert stats["undated_excluded"] == 0
    assert stats["trades"] == 2
    assert stats["total_pnl"] == -4.0  # only the in-range trades count
    # Unfiltered stats carry no range.
    plain = compute_stats(MULTI_DAY)
    assert plain["date_from"] is None and plain["date_to"] is None
    assert plain["trades"] == 4


def test_format_report_date_range_header():
    out = format_report(compute_stats(MULTI_DAY, "2026-10-07", "2026-10-08"))
    assert "Date range:" in out
    assert "2026-10-07 -> 2026-10-08" in out
    out_plain = format_report(compute_stats(MULTI_DAY))
    assert "Date range:" not in out_plain


def test_format_json_carries_date_range():
    payload = json.loads(format_json(
        compute_stats(MULTI_DAY, date_from="2026-10-07")))
    assert payload["stats"]["date_from"] == "2026-10-07"
    assert payload["stats"]["date_to"] is None


def test_cmd_stats_date_filter_end_to_end():
    """agent.py stats --from/--to filters the journal; range shown in report."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in MULTI_DAY:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, date_from="2026-10-07", date_to="2026-10-08")
        out = buf.getvalue()
    assert "Date range:" in out
    assert "Trades analyzed:    2" in out


def test_cmd_stats_bad_date_exits_2():
    """Garbage --from prints a friendly error and exits 2 (not a traceback)."""
    import io
    from contextlib import redirect_stderr
    from types import SimpleNamespace

    import agent
    guards = SimpleNamespace(base_dir=tempfile.mkdtemp())
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            agent.cmd_stats(guards, date_from="not-a-date")
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit")
    assert "bad date" in err.getvalue()


def test_normalize_symbols():
    assert normalize_symbols(None) == []
    assert normalize_symbols("") == []
    assert normalize_symbols("aapl, MSFT ,aapl,") == ["AAPL", "MSFT"]
    assert normalize_symbols(["tsla", " TSLA "]) == ["TSLA"]
    assert normalize_symbols(["", "  "]) == []


def test_filter_trades_by_symbols():
    kept, excluded = filter_trades_by_symbols(FIXTURE, "aapl")
    assert [t["symbol"] for t in kept] == ["AAPL", "AAPL"]
    assert excluded == 1


def test_filter_trades_by_symbols_passthrough():
    kept, excluded = filter_trades_by_symbols(FIXTURE)
    assert len(kept) == len(FIXTURE) and kept is not FIXTURE
    assert excluded == 0


def test_filter_trades_by_symbols_symbol_less_excluded():
    trades = FIXTURE + [trade("2026-10-09T11:00:00", None, "buy", 1, 50.0, 0.0)]
    kept, excluded = filter_trades_by_symbols(trades, "AAPL")
    assert len(kept) == 2
    assert excluded == 2  # MSFT + the symbol-less trade


def test_compute_stats_records_symbols():
    s = compute_stats(FIXTURE, symbols="aapl,msft")
    assert s["trades"] == 3
    assert s["symbols"] == ["AAPL", "MSFT"]
    assert s["symbol_excluded"] == 0


def test_compute_stats_symbols_compose_with_date_range():
    s = compute_stats(MULTI_DAY, date_from="2026-10-07", symbols="msft,nvda")
    assert s["trades"] == 2  # MSFT + NVDA, both in range
    assert s["symbols"] == ["MSFT", "NVDA"]
    assert s["symbol_excluded"] == 1  # the in-range AAPL trade


def test_format_report_symbols_header():
    out = format_report(compute_stats(MULTI_DAY, symbols="nvda"))
    assert "Symbols:            NVDA (3 other-symbol trade(s) excluded)" in out
    assert "Symbols:" not in format_report(compute_stats(MULTI_DAY))


def test_format_json_carries_symbols():
    payload = json.loads(format_json(compute_stats(MULTI_DAY, symbols="AAPL")))
    assert payload["stats"]["symbols"] == ["AAPL"]
    assert payload["stats"]["symbol_excluded"] == 2


def test_cmd_stats_symbols_end_to_end():
    """agent.py stats --symbols restricts the report to those tickers."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in MULTI_DAY:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, symbols="msft")
        out = buf.getvalue()
    assert "Symbols:            MSFT" in out
    assert "Trades analyzed:    1" in out


def dd_trade(d, time, pnl, symbol="AAPL"):
    """Dated-trade helper for drawdown-window tests (date may be None)."""
    return {"date": d, "time": time, "symbol": symbol, "side": "buy",
            "quantity": 1, "price": 100.0, "order_id": f"dd-{d}-{time}",
            "pnl": pnl, "paper": True}


def test_drawdown_window_dates():
    # Cumulative: 100 -> 60 -> -20 ; peak 2026-10-01, trough 2026-10-03.
    trades = [
        dd_trade("2026-10-01", "09:30:00", 100.0),
        dd_trade("2026-10-02", "09:30:00", -40.0),
        dd_trade("2026-10-03", "09:30:00", -80.0),
    ]
    s = compute_stats(trades)
    assert s["max_drawdown"] == 120.0
    assert s["max_drawdown_start"] == "2026-10-01"
    assert s["max_drawdown_end"] == "2026-10-03"
    assert s["max_drawdown_days"] == 2


def test_drawdown_window_first_occurrence_wins():
    trades = [
        dd_trade("2026-10-01", "09:30:00", 50.0),
        dd_trade("2026-10-02", "09:30:00", -50.0),  # dd 50: 10-01 -> 10-02
        dd_trade("2026-10-03", "09:30:00", 50.0),   # back to the peak
        dd_trade("2026-10-04", "09:30:00", -50.0),  # dd 50 again, no move
    ]
    s = compute_stats(trades)
    assert s["max_drawdown"] == 50.0
    assert s["max_drawdown_start"] == "2026-10-01"
    assert s["max_drawdown_end"] == "2026-10-02"


def test_drawdown_window_none_when_no_drawdown():
    s = compute_stats([dd_trade("2026-10-01", "09:30:00", 25.0)])
    assert s["max_drawdown"] == 0.0
    assert s["max_drawdown_start"] is None
    assert s["max_drawdown_end"] is None
    assert s["max_drawdown_days"] is None


def test_drawdown_window_undated_peak():
    # Peak trade has no date: depth is still measured; the partial window is
    # suppressed in the report (a half window would mislead).
    trades = [
        dd_trade(None, "09:30:00", 50.0),
        dd_trade("2026-10-02", "09:31:00", -60.0),
    ]
    s = compute_stats(trades)
    assert s["max_drawdown"] == 60.0
    assert s["max_drawdown_start"] is None
    assert s["max_drawdown_end"] == "2026-10-02"
    assert s["max_drawdown_days"] is None
    assert "Max drawdown:       $60.00 (" not in format_report(s)


def test_format_report_drawdown_window():
    trades = [
        dd_trade("2026-10-01", "09:30:00", 100.0),
        dd_trade("2026-10-03", "09:30:00", -120.0),
    ]
    out = format_report(compute_stats(trades))
    assert "Max drawdown:       $120.00 (2026-10-01 -> 2026-10-03, 2d)" in out


def test_format_report_no_drawdown_window_parens():
    out = format_report(compute_stats([dd_trade("2026-10-01", "09:30:00", 25.0)]))
    assert "Max drawdown:       $0.00" in out
    assert "Max drawdown:       $0.00 (" not in out


def test_format_json_carries_drawdown_window():
    trades = [
        dd_trade("2026-10-01", "09:30:00", 100.0),
        dd_trade("2026-10-03", "09:30:00", -120.0),
    ]
    payload = json.loads(format_json(compute_stats(trades)))
    assert payload["stats"]["max_drawdown_start"] == "2026-10-01"
    assert payload["stats"]["max_drawdown_end"] == "2026-10-03"
    assert payload["stats"]["max_drawdown_days"] == 2


def test_cmd_stats_symbols_empty_journal_message():
    """With a filter set but no paper trades at all, the message names the filters."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, symbols="MSFT")
        out = buf.getvalue()
    assert "Trades analyzed:    0" in out
    assert "No paper trades in the journal for those filters." in out


def _csv_rows(text):
    import csv
    import io
    return list(csv.reader(io.StringIO(text)))


def test_format_csv_header_and_sections():
    """CSV has a header and one row per section (summary, best, worst, day, symbol)."""
    rows = _csv_rows(format_csv(compute_stats(FIXTURE)))
    assert rows[0] == ["section", "key", "trades", "wins", "losses", "pnl"]
    body = rows[1:]
    assert [r[0] for r in body] == ["summary", "best", "worst", "day", "symbol", "symbol"]
    assert body[0][1] == "ALL"
    assert body[3][1] == "2026-10-09"
    assert [r[1] for r in body[4:]] == ["AAPL", "MSFT"]  # sorted


def test_format_csv_values_match_stats():
    rows = _csv_rows(format_csv(compute_stats(FIXTURE)))
    summary, best, worst, day, aapl, msft = rows[1:]
    assert summary[2:] == ["3", "1", "1", "5.00"]
    assert best == ["best", "AAPL 2026-10-09", "1", "1", "0", "10.00"]
    assert worst == ["worst", "AAPL 2026-10-09", "1", "0", "1", "-5.00"]
    assert day[2:] == ["3", "1", "1", "5.00"]
    assert aapl[2:] == ["2", "1", "1", "5.00"]
    assert msft[2:] == ["1", "0", "0", "0.00"]


def test_format_csv_money_is_plain_decimal():
    """Money columns parse as floats — no $ signs or thousands separators."""
    trades = [trade("2026-10-09T09:31:00", "AAPL", "buy", 10, 200.0, 1234.56)]
    rows = _csv_rows(format_csv(compute_stats(trades)))
    for row in rows[1:]:
        assert row[5] == "1234.56"  # not "1,234.56" or "$1,234.56"
        assert float(row[5]) == 1234.56


def test_format_csv_empty_stats_is_valid():
    rows = _csv_rows(format_csv(compute_stats([])))
    assert rows[0] == ["section", "key", "trades", "wins", "losses", "pnl"]
    assert len(rows) == 2  # header + summary row, no day/symbol rows
    assert rows[1][:2] == ["summary", "ALL"]
    assert rows[1][2:] == ["0", "0", "0", "0.00"]


def test_cmd_stats_csv_end_to_end():
    """agent.py stats --csv prints parseable CSV, not the text report."""
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in FIXTURE_WITH_LIVE:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent.cmd_stats(guards, as_csv=True)
        out = buf.getvalue()
    rows = _csv_rows(out)
    assert rows[0] == ["section", "key", "trades", "wins", "losses", "pnl"]
    assert "Paper journal stats" not in out  # no text-report bleed
    assert rows[1][0] == "summary"
    assert rows[1][2:] == ["3", "1", "1", "5.00"]  # live NVDA trade excluded


def test_cmd_stats_csv_live_note_goes_to_stderr():
    """The live-trade exclusion note must not pollute the CSV on stdout."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from types import SimpleNamespace

    import agent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "paper_journal.jsonl"), "w") as f:
            for t in FIXTURE_WITH_LIVE:
                f.write(json.dumps(t) + "\n")
        guards = SimpleNamespace(base_dir=tmp)
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            agent.cmd_stats(guards, as_csv=True)
    _csv_rows(out_buf.getvalue())  # parses cleanly
    assert "live" in err_buf.getvalue()


def test_cmd_stats_json_and_csv_conflict_exits_2():
    from types import SimpleNamespace

    import agent
    guards = SimpleNamespace(base_dir="/nonexistent-dir-no-trades")
    try:
        agent.cmd_stats(guards, as_json=True, as_csv=True)
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit(2) for --json + --csv")


def test_best_worst_trade_basics():
    """Best/worst realized trades carry symbol, date, and pnl."""
    stats = compute_stats(FIXTURE)
    assert stats["best_trade"] == {"symbol": "AAPL", "date": "2026-10-09",
                                   "pnl": 10.0}
    assert stats["worst_trade"] == {"symbol": "AAPL", "date": "2026-10-09",
                                    "pnl": -5.0}


def test_best_worst_trade_tie_first_wins():
    """Ties keep the first trade, like the drawdown window does."""
    trades = [
        trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0),
        trade("2026-10-09T10:31:00", "MSFT", "buy", 1, 400.0, 10.0),
        trade("2026-10-09T11:31:00", "TSLA", "buy", 1, 300.0, -10.0),
        trade("2026-10-09T12:31:00", "NVDA", "buy", 1, 150.0, -10.0),
    ]
    stats = compute_stats(trades)
    assert stats["best_trade"]["symbol"] == "AAPL"
    assert stats["worst_trade"]["symbol"] == "TSLA"


def test_best_worst_trade_none_when_no_realized():
    """Zero-pnl-only journals have no best/worst trade."""
    trades = [trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 0.0)]
    stats = compute_stats(trades)
    assert stats["best_trade"] is None
    assert stats["worst_trade"] is None
    out = format_report(stats)
    assert "  Best trade:         n/a" in out
    assert "  Worst trade:        n/a" in out
    rows = _csv_rows(format_csv(stats))
    sections = [r[0] for r in rows[1:]]
    assert "best" not in sections and "worst" not in sections


def test_best_worst_trade_undated():
    """A trade with no date still reports, marked undated."""
    t = trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0)
    del t["date"]
    stats = compute_stats([t])
    assert stats["best_trade"] == {"symbol": "AAPL", "date": None,
                                   "pnl": 10.0}
    out = format_report(stats)
    assert "Best trade:         $10.00 (AAPL, undated)" in out
    rows = _csv_rows(format_csv(stats))
    sections = {r[0]: r for r in rows[1:]}
    assert sections["best"] == ["best", "AAPL undated", "1", "1", "0", "10.00"]
    assert sections["worst"] == ["worst", "AAPL undated", "1", "1", "0", "10.00"]


def test_best_worst_trade_unknown_symbol_fallback():
    """Symbol-less trades report as UNKNOWN, like by_symbol does."""
    t = trade("2026-10-09T09:31:00", "AAPL", "buy", 1, 200.0, 10.0)
    del t["symbol"]
    stats = compute_stats([t])
    assert stats["best_trade"]["symbol"] == "UNKNOWN"


def test_format_report_best_worst_lines():
    out = format_report(compute_stats(FIXTURE))
    assert "  Best trade:         $10.00 (AAPL, 2026-10-09)" in out
    assert "  Worst trade:        $-5.00 (AAPL, 2026-10-09)" in out


def test_format_json_carries_best_worst():
    payload = json.loads(format_json(compute_stats(FIXTURE)))
    assert payload["stats"]["best_trade"]["pnl"] == 10.0
    assert payload["stats"]["worst_trade"]["pnl"] == -5.0
    empty = json.loads(format_json(compute_stats([])))
    assert empty["stats"]["best_trade"] is None
    assert empty["stats"]["worst_trade"] is None
