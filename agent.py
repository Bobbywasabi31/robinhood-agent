#!/usr/bin/env python3
"""
Robinhood Agentic Trading agent — hardened.

Commands:
  status              Account, positions, available tools
  quote SYMBOL        Current quote
  buy SYMBOL QTY [TARGET] [STOP]   Buy (thesis + guardrails + approval)
  sell SYMBOL QTY [TARGET] [STOP]  Sell (thesis + guardrails + approval)
  paper SYMBOL QTY    Simulate a buy without touching the API
  journal             Today's trade journal + P&L summary
  stats [--json|--csv|--trades|--equity] [--from DATE] [--to DATE] [--days N]
        [--months N] [--symbols A,B] [--top N] [--help]   Paper-journal stats
  tools               List raw MCP tools from the server

Auth: export ROBINHOOD_MCP_TOKEN="..."
Safety: guardrails.json — trading_enabled defaults to false,
        paper_trading defaults to true.
"""
import json
import os
import re
import sys

from mcp_client import RobinhoodMCPClient, MCPError, MCPAuthError
from guardrails import Guardrails, load_universe
from thesis import build_thesis, save_thesis


def get_client():
    token = os.environ.get("ROBINHOOD_MCP_TOKEN")
    if not token:
        print("Set ROBINHOOD_MCP_TOKEN first. See README.md for OAuth setup.")
        sys.exit(1)
    client = RobinhoodMCPClient(access_token=token)
    try:
        info = client.initialize()
        server = (info or {}).get("serverInfo", {})
        if server:
            print(f"Connected to {server.get('name', 'MCP server')} v{server.get('version', '?')}")
    except MCPAuthError as e:
        print(f"Authentication failed: {e}")
        print("Your token may have expired. Re-run the OAuth flow (see README).")
        sys.exit(1)
    except MCPError as e:
        print(f"Connection failed: {e}")
        sys.exit(1)
    return client


def extract_price(quote_result):
    """Best-effort price extraction from a quote tool result."""
    text = json.dumps(quote_result)
    for field in ("last_trade_price", "last_price", "mark_price",
                 "ask_price", "bid_price", "price", "last"):
        m = re.search(rf'"{field}"\s*:\s*"?([\d.]+)"?', text)
        if m:
            try:
                p = float(m.group(1))
                if p > 0:
                    return p
            except ValueError:
                continue
    return 0


def get_price(client, symbol):
    tool = client.find_tool("quote") or client.find_tool("price")
    if not tool:
        return 0, "no quote tool on server"
    try:
        result = client.call_tool(tool, {"symbol": symbol.upper()})
        price = extract_price(result)
        return price, "" if price > 0 else "could not parse price"
    except MCPError as e:
        return 0, str(e)


def parse_optional_float(value, name):
    """Parse an optional CLI price arg; warn and skip on garbage input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        print(f"Ignoring invalid {name} price: {value!r}")
        return None


def cmd_status(client, guards):
    tools = client.list_tools()
    print(f"\nMCP tools available ({len(tools)}):")
    for t in sorted(tools, key=lambda x: x.get("name", "")):
        desc = (t.get("description") or "")[:80]
        print(f"  {t['name']:40s} {desc}")
    # Account snapshot via common tool names
    for kw in (["account"], ["portfolio"], ["position"], ["balance"]):
        name = client.find_tool(*kw)
        if name and "quote" not in name:
            try:
                r = client.call_tool(name, {})
                print(f"\n== {name} ==\n{json.dumps(r, indent=2)[:1500]}")
            except MCPError as e:
                print(f"  ({name}: {e})")


def get_sector(symbol):
    """Look up GICS sector from the universe file."""
    uni = load_universe()
    for s in uni.get("sp500", []):
        if s.upper() == symbol.upper():
            break
    # sector lookup from universe entries if available
    for entry in uni.get("entries", []):
        if entry.get("symbol", "").upper() == symbol.upper():
            return entry.get("sector", "Unknown")
    return "Unknown"


def cmd_trade(client, guards, side, symbol, qty_str, paper=False,
              target=None, stop=None):
    symbol = symbol.upper()
    try:
        qty = float(qty_str)
    except ValueError:
        print("Quantity must be a number.")
        return

    price, err = get_price(client, symbol)
    if price <= 0:
        msg = f"REFUSED {side} {qty} {symbol}: no price ({err})"
        print(msg)
        guards.log(msg)
        return

    allowed, reason = guards.check(symbol, side, qty, price)
    if not allowed:
        msg = f"BLOCKED {side} {qty} {symbol} @ ${price:.2f}: {reason}"
        print(msg)
        guards.log(msg)
        return

    # --- Investment thesis: one page on risk and reward ---
    if target is None:
        try:
            target = float(
                input("Bull-case target price (or Enter to skip): ").strip() or 0
            ) or None
        except ValueError:
            target = None
    if stop is None:
        try:
            stop = float(input("Stop-loss price (or Enter to skip): ").strip() or 0) or None
        except ValueError:
            stop = None

    uni = load_universe()
    universe_note = ""
    if symbol in [s.upper() for s in uni.get("materials", [])]:
        universe_note = "S&P 500 Materials"
    elif symbol in [s.upper() for s in uni.get("sp500", [])]:
        universe_note = "S&P 500"

    thesis_text, verdict = build_thesis(symbol, side, qty, price, {
        "sector": get_sector(symbol),
        "reason": "Manual trade request via agent CLI",
        "target_price": target,
        "stop_price": stop,
        "universe_note": universe_note,
    })
    thesis_path = save_thesis(symbol, side, thesis_text)

    print()
    print(thesis_text)
    print()
    print(f"Thesis saved to {thesis_path}")
    guards.log(f"THESIS {side} {qty} {symbol}: justified={verdict['justified']} "
               f"ratio={verdict['risk_reward_ratio']} -> {thesis_path}")

    if not verdict["justified"]:
        print()
        print("!! Thesis is NOT justified (missing target/stop or ratio < 2:1).")
        print("   Fix the thesis before trading real money.")

    notional = qty * price
    mode = "PAPER" if (paper or guards.config["paper_trading"]) else "LIVE"
    print(f"\n[{mode}] {side.upper()} {qty} {symbol} @ ~${price:.2f} = ${notional:.2f}")

    if guards.config["require_approval"]:
        ans = input("Approve? (you have read the thesis above) [y/N] ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            guards.log(f"CANCELLED {side} {qty} {symbol} (user declined)")
            return

    is_paper = paper or guards.config["paper_trading"]
    if is_paper:
        # Simulate: assume fill at quoted price
        order_id = f"paper-{int(__import__('time').time())}"
        guards.record_trade(symbol, side, qty, price, order_id=order_id, paper=True)
        guards.log(f"PAPER {side} {qty} {symbol} @ ${price:.2f} (simulated fill)")
        print(f"Paper trade recorded (simulated fill @ ${price:.2f}). No real order sent.")
        return

    order_tool = (client.find_tool("order") or client.find_tool("trade")
                  or client.find_tool(side))
    if not order_tool:
        print("No order tool found on server.")
        return
    try:
        result = client.call_tool(order_tool, {
            "symbol": symbol, "side": side.lower(), "quantity": qty,
            "type": "market", "time_in_force": "day",
        })
        print(f"Order result:\n{json.dumps(result, indent=2)[:1500]}")
        order_id = extract_order_id(result)
        guards.record_trade(symbol, side, qty, price, order_id=order_id)
        guards.log(f"LIVE {side} {qty} {symbol} @ ${price:.2f} order_id={order_id}")
    except MCPError as e:
        print(f"Order failed: {e}")
        guards.log(f"FAILED {side} {qty} {symbol}: {e}")


def extract_order_id(result):
    text = json.dumps(result)
    m = re.search(r'"(?:order_?id|id|ref_?id)"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else None


def cmd_stats(guards, as_json=False, as_csv=False, as_trades=False,
              as_equity=False, date_from=None, date_to=None, symbols=None,
              top_n=None):
    """Read-only paper-journal stats. Never touches the API or trading logic.

    Merges the append-only journal (long-term history) with today's state file
    (which rolls daily) so stats survive midnight; duplicates are dropped.
    as_json prints the same stats as machine-readable JSON instead of text;
    as_csv prints the daily + monthly + per-symbol P&L tables as CSV instead of text.
    as_trades prints one CSV row per paper trade (same --from/--to/--days/
    --months/--symbols filters apply; --top is a leaderboard filter and is
    ignored with a note); as_trades + as_json renders the same rows as
    machine-readable JSON instead (live-trade exclusion count in the
    payload).
    as_equity prints the daily equity curve as CSV (date,daily_pnl,cumulative_pnl;
    same filters apply; --top is ignored with a note; trades without a valid
    date are skipped with a stderr note); as_equity + as_json renders the
    same series as machine-readable JSON (undated/live exclusion counts in
    the payload).
    as_csv can't combine with as_json (the report's JSON and CSV are
    separate exports); as_json alone prints the main report as JSON.
    date_from/date_to (YYYY-MM-DD, inclusive) restrict to a date range.
    days (positive int): shortcut for the last N days ending today — fills
    any bound an explicit --from/--to doesn't set. months (positive int):
    shortcut for the last N calendar months incl. this one (first day of
    month N-1 back through today) — fills any bound an explicit --from/--to
    doesn't set; can't be combined with days.
    symbols ("AAPL,MSFT", case-insensitive) restricts to those tickers.
    top_n (positive int): per-symbol section shows only the top N + bottom N
    tickers by realized P&L (a leaderboard).
    """
    from paper_stats import (
        compute_stats, default_append_journal_path, format_csv, format_json,
        format_report, format_trades_csv, format_trades_json,
        format_equity_csv, format_equity_json,
        load_append_journal, load_journal, merge_trades, prepare_trades,
    )
    if sum(bool(m) for m in (as_csv, as_trades, as_equity)) > 1:
        print("stats: use only one of --csv, --trades or --equity",
              file=sys.stderr)
        sys.exit(2)
    if as_json and as_csv:
        print("stats: --json can't combine with --csv "
              "(use --trades --json or --equity --json)",
              file=sys.stderr)
        sys.exit(2)
    trades = merge_trades(
        load_append_journal(default_append_journal_path(guards.base_dir)),
        load_journal(os.path.join(guards.base_dir, ".agent_state.json")),
    )
    paper = [t for t in trades if t.get("paper")]
    live = [t for t in trades if not t.get("paper")]
    try:
        if as_trades or as_equity:
            listed, _, _ = prepare_trades(paper, date_from=date_from,
                                          date_to=date_to, symbols=symbols)
        else:
            stats = compute_stats(paper, date_from=date_from, date_to=date_to,
                                  symbols=symbols, top_n=top_n)
    except ValueError as e:
        print(f"stats: bad date: {e}", file=sys.stderr)
        sys.exit(2)
    if as_equity:
        if top_n is not None:
            print("stats: --top is ignored with --equity (the curve is a "
                  "daily series, not a leaderboard)", file=sys.stderr)
        if as_json:
            print(format_equity_json(listed, live_excluded=len(live)))
            return
        if live:
            print(f"stats: {len(live)} live trade(s) excluded from the "
                  "equity curve", file=sys.stderr)
        text, undated = format_equity_csv(listed)
        if undated:
            print(f"stats: {undated} trade(s) without a valid date skipped "
                  "from the equity curve", file=sys.stderr)
        print(text, end="")
        return
    if as_trades:
        if top_n is not None:
            print("stats: --top is ignored with --trades (per-trade "
                  "listing is not a leaderboard)", file=sys.stderr)
        if as_json:
            print(format_trades_json(listed, live_excluded=len(live)))
            return
        if live:
            print(f"stats: {len(live)} live trade(s) excluded from the "
                  "per-trade CSV", file=sys.stderr)
        print(format_trades_csv(listed), end="")
        return
    if as_csv:
        if live:
            print(f"stats: {len(live)} live trade(s) excluded from CSV",
                  file=sys.stderr)
        if stats.get("by_symbol_omitted"):
            print(f"stats: --top {stats['top_n']}: "
                  f"{stats['by_symbol_omitted']} symbol(s) not shown in CSV",
                  file=sys.stderr)
        print(format_csv(stats), end="")
        return
    if as_json:
        print(format_json(stats, live_excluded=len(live)))
        return
    print(format_report(stats))
    if live:
        print(f"({len(live)} live trade(s) in the journal — excluded from paper stats.)")
    if not paper:
        if date_from or date_to or symbols or top_n:
            print("No paper trades in the journal for those filters.")
        else:
            print("No paper trades in the journal yet.")


def cmd_journal(guards):
    s = guards.daily_summary()
    print("\nToday's journal:")
    print(f"  Trades: {s['trades']} (paper: {s['paper_trades']}, live: {s['live_trades']})")
    print(f"  Realized P&L: ${s['realized_pnl']:.2f}")
    for t in guards._trades_today:
        if t["date"] == __import__("datetime").date.today().isoformat():
            flag = "PAPER" if t.get("paper") else "LIVE"
            print(f"  [{flag}] {t['time'][11:19]} {t['side'].upper()} {t['quantity']} "
                  f"{t['symbol']} @ ${t['price']:.2f}")




STATS_SWITCH_FLAGS = ("--json", "--csv", "--trades", "--equity")
STATS_VALUE_FLAGS = ("--from", "--to", "--days", "--months", "--symbols", "--top")

STATS_USAGE = """\
Usage: python3 agent.py stats [options]

Read-only paper-journal stats. Never touches the API or trading logic.

  --from YYYY-MM-DD   restrict to a date range (inclusive)
  --to YYYY-MM-DD     restrict to a date range (inclusive)
  --days N            last N days incl. today (explicit --from/--to wins)
  --months N          last N calendar months incl. this one (explicit
                      --from/--to wins; can't combine with --days)
  --symbols A,B       restrict to specific tickers (case-insensitive)
  --top N             per-symbol leaderboard: top N + bottom N tickers
  --json              machine-readable JSON instead of the text report
  --csv               daily + monthly + per-symbol P&L as CSV
  --trades            one CSV row per paper trade
  --trades --json     same per-trade rows as machine-readable JSON
  --equity            daily equity curve as CSV
  --equity --json     same equity curve as machine-readable JSON
  --help, -h          show this help
"""


class StatsUsageError(ValueError):
    """Unknown or malformed stats flag."""


def parse_stats_args(args):
    """Split a stats argv tail into (flags, values, help_requested).

    flags: set of switch flags present; values: value-flag -> following
    token (None when the flag trails with nothing after it).
    Raises StatsUsageError on any unknown -/-- token, so a typo'd flag
    fails loudly instead of being silently ignored.
    """
    flags = set()
    values = {}
    help_requested = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--help", "-h"):
            help_requested = True
        elif a in STATS_SWITCH_FLAGS:
            flags.add(a)
        elif a in STATS_VALUE_FLAGS:
            values[a] = args[i + 1] if i + 1 < len(args) else None
            i += 1
        elif a.startswith("-") and a != "-":
            raise StatsUsageError(f"unknown flag: {a}")
        i += 1
    return flags, values, help_requested


def resolve_range_window(date_from, date_to, days, months):
    """Apply the --days/--months shortcuts to fill unset --from/--to bounds.

    Returns (date_from, date_to). A shortcut fills only the bounds an
    explicit --from/--to didn't set. --days and --months can't be combined;
    garbage values raise ValueError with a human-readable message.
    """
    if days is not None and months is not None:
        raise ValueError("--days and --months can't be combined")
    if days is not None:
        from paper_stats import resolve_days_range
        days_from, days_to = resolve_days_range(days)
        if date_from is None:
            date_from = days_from
        if date_to is None:
            date_to = days_to
    elif months is not None:
        from paper_stats import resolve_months_range
        months_from, months_to = resolve_months_range(months)
        if date_from is None:
            date_from = months_from
        if date_to is None:
            date_to = months_to
    return date_from, date_to


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    guards = Guardrails()
    cmd = sys.argv[1].lower()

    if cmd == "journal":
        cmd_journal(guards)
        return
    if cmd == "stats":
        rest = sys.argv[2:]
        try:
            flags, values, show_help = parse_stats_args(rest)
        except StatsUsageError as e:
            print(f"stats: {e}", file=sys.stderr)
            print(STATS_USAGE, file=sys.stderr)
            sys.exit(2)
        if show_help:
            print(STATS_USAGE)
            return
        date_from = values.get("--from")
        date_to = values.get("--to")
        try:
            date_from, date_to = resolve_range_window(
                date_from, date_to, values.get("--days"), values.get("--months"))
        except ValueError as e:
            print(f"stats: {e}", file=sys.stderr)
            sys.exit(2)
        top = values.get("--top")
        top_n = None
        if top is not None:
            from paper_stats import resolve_top_n
            try:
                top_n = resolve_top_n(top)
            except ValueError as e:
                print(f"stats: {e}", file=sys.stderr)
                sys.exit(2)
        cmd_stats(guards, as_json="--json" in flags, as_csv="--csv" in flags,
                  as_trades="--trades" in flags, as_equity="--equity" in flags,
                  date_from=date_from,
                  date_to=date_to,
                  symbols=values.get("--symbols"),
                  top_n=top_n)
        return
    if cmd == "tools":
        client = get_client()
        for t in sorted(client.list_tools(), key=lambda x: x.get("name", "")):
            print(f"{t['name']}: {(t.get('description') or '')[:100]}")
        return

    client = get_client()
    if cmd == "status":
        cmd_status(client, guards)
    elif cmd == "quote" and len(sys.argv) > 2:
        price, err = get_price(client, sys.argv[2])
        print(f"{sys.argv[2].upper()}: ${price:.2f}" if price > 0 else f"Quote failed: {err}")
    elif cmd in ("buy", "sell") and len(sys.argv) > 3:
        target = parse_optional_float(sys.argv[4] if len(sys.argv) > 4 else None, "target")
        stop = parse_optional_float(sys.argv[5] if len(sys.argv) > 5 else None, "stop")
        cmd_trade(client, guards, cmd, sys.argv[2], sys.argv[3],
                  target=target, stop=stop)
    elif cmd == "paper" and len(sys.argv) > 3:
        target = parse_optional_float(sys.argv[4] if len(sys.argv) > 4 else None, "target")
        stop = parse_optional_float(sys.argv[5] if len(sys.argv) > 5 else None, "stop")
        cmd_trade(client, guards, "buy", sys.argv[2], sys.argv[3],
                  paper=True, target=target, stop=stop)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
