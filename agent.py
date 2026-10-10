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
  stats [--json] [--from DATE] [--to DATE]   Paper stats for a date range (DATE = YYYY-MM-DD)
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


def cmd_stats(guards, as_json=False, date_from=None, date_to=None):
    """Read-only paper-journal stats. Never touches the API or trading logic.

    Merges the append-only journal (long-term history) with today's state file
    (which rolls daily) so stats survive midnight; duplicates are dropped.
    as_json prints the same stats as machine-readable JSON instead of text.
    date_from/date_to (YYYY-MM-DD, inclusive) restrict to a date range.
    """
    from paper_stats import (
        compute_stats, default_append_journal_path, format_json, format_report,
        load_append_journal, load_journal, merge_trades,
    )
    trades = merge_trades(
        load_append_journal(default_append_journal_path(guards.base_dir)),
        load_journal(os.path.join(guards.base_dir, ".agent_state.json")),
    )
    paper = [t for t in trades if t.get("paper")]
    live = [t for t in trades if not t.get("paper")]
    try:
        stats = compute_stats(paper, date_from=date_from, date_to=date_to)
    except ValueError as e:
        print(f"stats: bad date: {e}", file=sys.stderr)
        sys.exit(2)
    if as_json:
        print(format_json(stats, live_excluded=len(live)))
        return
    print(format_report(stats))
    if live:
        print(f"({len(live)} live trade(s) in the journal — excluded from paper stats.)")
    if not paper:
        if date_from or date_to:
            print("No paper trades in the journal for that date range.")
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


def _flag_value(args, *names):
    """Value of the first matching --flag in args, or None."""
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            return args[i + 1]
    return None


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
        cmd_stats(guards, as_json="--json" in rest,
                  date_from=_flag_value(rest, "--from"),
                  date_to=_flag_value(rest, "--to"))
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
