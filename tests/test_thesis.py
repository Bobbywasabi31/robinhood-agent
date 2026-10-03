"""Tests for the investment thesis generator."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from thesis import build_thesis, save_thesis


def test_justified_thesis():
    # $100 entry, $120 target, $90 stop -> 2:1 ratio
    text, verdict = build_thesis("LIN", "buy", 10, 100.0, {
        "sector": "Materials",
        "reason": "test",
        "target_price": 120.0,
        "stop_price": 90.0,
        "universe_note": "S&P 500 Materials",
    })
    assert verdict["justified"] is True
    assert verdict["risk_reward_ratio"] == 2.0
    assert "JUSTIFIED" in text
    assert "LIN" in text and "Materials" in text


def test_weak_ratio_not_justified():
    # $100 entry, $105 target, $90 stop -> 0.5:1 ratio
    text, verdict = build_thesis("LIN", "buy", 10, 100.0, {
        "target_price": 105.0,
        "stop_price": 90.0,
    })
    assert verdict["justified"] is False
    assert verdict["risk_reward_ratio"] == 0.5
    assert "NOT JUSTIFIED" in text


def test_missing_target_stop():
    text, verdict = build_thesis("AAPL", "buy", 1, 200.0, {})
    assert verdict["justified"] is False
    assert verdict["risk_reward_ratio"] is None
    assert "no target + no stop = no trade" in text


def test_thesis_covers_required_sections():
    text, _ = build_thesis("SHW", "buy", 5, 300.0, {
        "target_price": 360.0, "stop_price": 270.0,
    })
    for section in ["The Trade", "Bull Case", "Bear Case", "Risk / Reward",
                    "Why Now", "Position Sizing", "Invalidation", "Verdict"]:
        assert section in text, f"missing section: {section}"


def test_save_thesis():
    text, _ = build_thesis("APD", "buy", 2, 250.0, {
        "target_price": 300.0, "stop_price": 225.0,
    })
    path = save_thesis("APD", "buy", text)
    assert os.path.exists(path)
    assert open(path).read() == text
    os.remove(path)  # cleanup


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
