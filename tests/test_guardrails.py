"""Unit tests for guardrails — the most critical code in this repo."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guardrails import Guardrails


def make_guards(**overrides):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "guardrails.json")
    cfg = {
        "trading_enabled": True,
        "paper_trading": True,
        "require_approval": False,
        "max_position_usd": 100.0,
        "max_daily_loss_usd": 50.0,
        "max_daily_trades": 10,
        "min_seconds_between_trades": 0,
        "buy_only": False,
        "allowed_symbols": [],
        "blocked_symbols": [],
    }
    cfg.update(overrides)
    with open(path, "w") as f:
        json.dump(cfg, f)
    return Guardrails(path)


def test_kill_switch_default_off():
    tmp = tempfile.mkdtemp()
    g = Guardrails(os.path.join(tmp, "nonexistent.json"))
    assert g.config["trading_enabled"] is False
    allowed, _ = g.check("AAPL", "buy", 1, 100)
    assert allowed is False


def test_kill_switch_blocks():
    g = make_guards(trading_enabled=False)
    allowed, reason = g.check("AAPL", "buy", 1, 100)
    assert allowed is False and "Kill switch" in reason


def test_position_cap():
    g = make_guards()
    allowed, _ = g.check("AAPL", "buy", 1, 50)
    assert allowed is True
    allowed, reason = g.check("AAPL", "buy", 3, 50)  # $150 > $100
    assert allowed is False and "exceeds max position" in reason


def test_blocklist():
    g = make_guards(blocked_symbols=["GME"])
    allowed, _ = g.check("GME", "buy", 1, 10)
    assert allowed is False
    allowed, _ = g.check("AAPL", "buy", 1, 10)
    assert allowed is True


def test_allowlist():
    g = make_guards(allowed_symbols=["AAPL", "MSFT"])
    allowed, _ = g.check("AAPL", "buy", 1, 10)
    assert allowed is True
    allowed, reason = g.check("TSLA", "buy", 1, 10)
    assert allowed is False and "allowlist" in reason


def test_buy_only():
    g = make_guards(buy_only=True)
    allowed, _ = g.check("AAPL", "buy", 1, 10)
    assert allowed is True
    allowed, _ = g.check("AAPL", "sell", 1, 10)
    assert allowed is False


def test_daily_trade_limit():
    g = make_guards(max_daily_trades=2)
    for _ in range(2):
        g.record_trade("AAPL", "buy", 1, 10)
    allowed, reason = g.check("AAPL", "buy", 1, 10)
    assert allowed is False and "Daily trade limit" in reason


def test_daily_loss_halt():
    g = make_guards()
    g.record_trade("AAPL", "buy", 1, 10)
    g.record_pnl(-60)  # lost $60, limit is $50
    allowed, reason = g.check("AAPL", "buy", 1, 10)
    assert allowed is False and "Daily loss limit" in reason


def test_no_price_no_trade():
    g = make_guards()
    allowed, reason = g.check("AAPL", "buy", 1, 0)
    assert allowed is False and "blind" in reason


def test_invalid_quantity():
    g = make_guards()
    allowed, _ = g.check("AAPL", "buy", 0, 100)
    assert allowed is False
    allowed, _ = g.check("AAPL", "buy", -5, 100)
    assert allowed is False


def test_unknown_config_key_fails():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "bad.json")
    with open(path, "w") as f:
        json.dump({"trading_enabled": True, "typo_key": 1}, f)
    try:
        Guardrails(path)
        assert False, "should have raised"
    except ValueError as e:
        assert "Unknown guardrail keys" in str(e)


def test_cooldown():
    g = make_guards(min_seconds_between_trades=3600)
    g.record_trade("AAPL", "buy", 1, 10)
    allowed, reason = g.check("AAPL", "buy", 1, 10)
    assert allowed is False and "Cooldown" in reason
    # Different symbol not affected
    allowed, _ = g.check("MSFT", "buy", 1, 10)
    assert allowed is True


def test_universe_sp500():
    g = make_guards(universe="sp500")
    allowed, _ = g.check("AAPL", "buy", 1, 10)
    assert allowed is True
    allowed, reason = g.check("GME", "buy", 1, 10)
    assert allowed is False and "universe" in reason


def test_universe_materials():
    g = make_guards(universe="materials")
    allowed, _ = g.check("LIN", "buy", 1, 10)
    assert allowed is True
    allowed, reason = g.check("AAPL", "buy", 1, 10)
    assert allowed is False and "universe" in reason


def test_universe_any():
    g = make_guards(universe="any")
    allowed, _ = g.check("GME", "buy", 1, 10)
    assert allowed is True


def test_universe_fail_closed_when_data_missing():
    import guardrails as gmod
    orig = gmod.load_universe
    gmod.load_universe = lambda: {"sp500": [], "materials": []}
    try:
        g = make_guards(universe="sp500")
        allowed, reason = g.check("AAPL", "buy", 1, 10)
        assert allowed is False and "Universe data unavailable" in reason
    finally:
        gmod.load_universe = orig

def test_record_trade_appends_to_append_journal():
    g = make_guards()
    jpath = os.path.join(g.base_dir, "paper_journal.jsonl")
    assert not os.path.exists(jpath)
    g.record_trade("AAPL", "buy", 1, 200.0, order_id="oid-1", paper=True)
    g.record_trade("MSFT", "sell", 2, 400.0, order_id="oid-2", paper=True)
    with open(jpath) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 2
    assert lines[0]["symbol"] == "AAPL" and lines[0]["order_id"] == "oid-1"
    assert lines[1]["symbol"] == "MSFT" and lines[1]["order_id"] == "oid-2"
    assert lines[0]["paper"] is True
    assert all("date" in e and "time" in e for e in lines)


def test_record_pnl_amends_last_journal_line():
    g = make_guards()
    jpath = os.path.join(g.base_dir, "paper_journal.jsonl")
    g.record_trade("AAPL", "buy", 1, 200.0, paper=True)
    g.record_pnl(12.5)
    with open(jpath) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    assert lines[0]["pnl"] == 12.5


def test_journal_append_failure_never_breaks_recording():
    g = make_guards()
    # Make the journal path a directory so appends fail — recording must survive.
    jpath = os.path.join(g.base_dir, "paper_journal.jsonl")
    os.mkdir(jpath)
    g.record_trade("AAPL", "buy", 1, 200.0, paper=True)
    assert len(g._trades_today) == 1
    g.record_pnl(5.0)
    assert g._trades_today[-1]["pnl"] == 5.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

