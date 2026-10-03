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

