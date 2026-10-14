"""Tests for agent.parse_stats_args: stats CLI flag handling.

Unknown --flags must fail loudly (StatsUsageError) instead of being
silently ignored, and --help/-h must request the usage text.
"""
import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
from agent import STATS_USAGE, StatsUsageError, parse_stats_args


def test_switches_collected():
    flags, values, help_requested = parse_stats_args(["--json"])
    assert flags == {"--json"}
    assert values == {}
    assert help_requested is False


def test_multiple_switches():
    flags, _, _ = parse_stats_args(["--csv", "--days", "7"])
    assert "--csv" in flags
    assert "--json" not in flags


def test_value_flags_take_next_token():
    flags, values, _ = parse_stats_args(
        ["--from", "2026-10-01", "--to", "2026-10-07",
         "--days", "7", "--symbols", "AAPL,MSFT", "--top", "5"])
    assert values == {"--from": "2026-10-01", "--to": "2026-10-07",
                      "--days": "7", "--symbols": "AAPL,MSFT", "--top": "5"}
    assert flags == set()


def test_trailing_value_flag_yields_none():
    _, values, _ = parse_stats_args(["--from"])
    assert values == {"--from": None}


def test_empty_args():
    flags, values, help_requested = parse_stats_args([])
    assert flags == set() and values == {} and help_requested is False


def test_help_long_and_short():
    for args in (["--help"], ["-h"], ["--json", "--help"]):
        _, _, help_requested = parse_stats_args(args)
        assert help_requested is True


def test_unknown_flag_raises():
    with pytest.raises(StatsUsageError, match="unknown flag: --jsonn"):
        parse_stats_args(["--jsonn"])


def test_unknown_single_dash_flag_raises():
    with pytest.raises(StatsUsageError, match="unknown flag: -x"):
        parse_stats_args(["-x"])


def test_unknown_flag_after_valid_flags_raises():
    with pytest.raises(StatsUsageError):
        parse_stats_args(["--from", "2026-10-01", "--bogus"])


def test_positional_tokens_ignored():
    flags, values, help_requested = parse_stats_args(["stats"])
    assert flags == set() and values == {} and help_requested is False


def test_bare_dash_ignored():
    flags, _, _ = parse_stats_args(["-", "--json"])
    assert flags == {"--json"}


def test_last_value_flag_wins():
    _, values, _ = parse_stats_args(["--days", "7", "--days", "30"])
    assert values == {"--days": "30"}


def test_stats_usage_error_is_value_error():
    assert issubclass(StatsUsageError, ValueError)


def test_usage_text_mentions_all_flags():
    for flag in ("--from", "--to", "--days", "--symbols", "--top",
                 "--json", "--csv", "--trades", "--equity", "--help"):
        assert flag in STATS_USAGE


def test_agent_module_exports_parse_helpers():
    assert callable(agent.parse_stats_args)
    assert isinstance(agent.STATS_USAGE, str)


def test_months_value_flag_captured():
    flags, values, _ = parse_stats_args(["--months", "3"])
    assert flags == set()
    assert values == {"--months": "3"}


def test_usage_text_mentions_months():
    assert "--months" in STATS_USAGE


def test_resolve_range_window_conflict_raises():
    try:
        agent.resolve_range_window(None, None, "7", "3")
    except ValueError as e:
        assert "--days and --months can't be combined" in str(e)
    else:
        raise AssertionError("expected ValueError for --days + --months")


def test_resolve_range_window_days_fills_unset_bounds():
    frm, to = agent.resolve_range_window(None, None, "7", None)
    from paper_stats import resolve_days_range
    assert (frm, to) == resolve_days_range("7")


def test_resolve_range_window_months_fills_unset_bounds():
    frm, to = agent.resolve_range_window(None, None, None, "3")
    from paper_stats import resolve_months_range
    assert (frm, to) == resolve_months_range("3")


def test_resolve_range_window_explicit_from_wins_over_months():
    from paper_stats import resolve_months_range
    _, expected_to = resolve_months_range("3")
    frm, to = agent.resolve_range_window("2026-01-01", None, None, "3")
    assert frm == "2026-01-01"
    assert to == expected_to


def test_resolve_range_window_garbage_months_raises():
    try:
        agent.resolve_range_window(None, None, None, "abc")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for garbage --months")


def test_resolve_range_window_no_shortcuts_passthrough():
    assert agent.resolve_range_window("2026-01-01", "2026-01-31",
                                      None, None) == ("2026-01-01", "2026-01-31")
    assert agent.resolve_range_window(None, None, None, None) == (None, None)
