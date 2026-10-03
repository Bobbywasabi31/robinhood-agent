#!/usr/bin/env python3
"""
Robinhood Agentic Trading agent.
Connects to Robinhood's official MCP server and trades within guardrails.

Usage:
  python3 agent.py status              # account status, positions
  python3 agent.py quote AAPL          # get a quote
  python3 agent.py buy AAPL 10         # buy 10 shares (requires approval unless disabled)
  python3 agent.py sell AAPL 5         # sell 5 shares

Auth: set ROBINHOOD_MCP_TOKEN env var after completing OAuth.
See README.md for the OAuth setup walkthrough.
"""
import json
import os
import sys

from mcp_client import RobinhoodMCPClient, MCPError
from guardrails import Guardrails


def get_client():
    token = os.environ.get("ROBINHOOD_MCP_TOKEN")
    if not token:
        print("Set ROBINHOOD_MCP_TOKEN first. See README.md for OAuth setup.")
        sys.exit(1)
    client = RobinhoodMCPClient(access_token=token)
    try:
        client.initialize()
    except MCPError as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)
    return client


def find_tool(client, keywords):
    """Find a tool whose name matches keywords (server tool names may vary)."""
    for tool in client.list_tools():
        name = tool.get("name", "").lower()
        if all(k.lower() in name for k in keywords):
            return tool["name"]
    return None


def cmd_status(client, guards):
    tools = {t["name"]: t for t in client.list_tools()}
    print(f"Available tools ({len(tools)}):")
    for name in sorted(tools):
        print(f"  - {name}")
    # Try common account/positions tools
    for keywords in (["account"], ["position"], ["portfolio"], ["balance"]):
        tool_name = find_tool(client, keywords)
        if tool_name:
            try:
                result = client.call_tool(tool_name, {})
                print(f"\n== {tool_name} ==\n{json.dumps(result, indent=2)[:2000]}")
            except MCPError as e:
                print(f"{tool_name} failed: {e}")


def cmd_quote(client, guards, symbol):
    tool_name = find_tool(client, ["quote"]) or find_tool(client, ["price"])
    if not tool_name:
        print("No quote tool found on server.")
        return
    try:
        result = client.call_tool(tool_name, {"symbol": symbol.upper()})
        print(json.dumps(result, indent=2)[:2000])
    except MCPError as e:
        print(f"Quote failed: {e}")


def cmd_trade(client, guards, side, symbol, quantity):
    symbol = symbol.upper()
    try:
        quantity = float(quantity)
    except ValueError:
        print("Quantity must be a number.")
        return

    # Get current price for guardrail notional check
    quote_tool = find_tool(client, ["quote"]) or find_tool(client, ["price"])
    price = 0
    if quote_tool:
        try:
            q = client.call_tool(quote_tool, {"symbol": symbol})
            # Try common price fields
            content = json.dumps(q)
            import re
            m = re.search(r'"(?:last|price|mark)[^"]*":\s*([\d.]+)', content)
            if m:
                price = float(m.group(1))
        except MCPError:
            pass
    if price <= 0:
        print("Could not get price — refusing to trade blind.")
        guards.log(f"REFUSED {side} {quantity} {symbol}: no price available")
        return

    allowed, reason = guards.check(symbol, side, quantity, price)
    if not allowed:
        print(f"Blocked by guardrails: {reason}")
        guards.log(f"BLOCKED {side} {quantity} {symbol} @ {price}: {reason}")
        return

    order_tool = find_tool(client, ["order"]) or find_tool(client, ["trade"]) or find_tool(client, [side])
    if not order_tool:
        print("No order tool found on server.")
        return

    print(f"\nProposed: {side.upper()} {quantity} {symbol} @ ~${price:.2f} = ${quantity*price:.2f}")
    if guards.config["require_approval"]:
        answer = input("Approve? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            guards.log(f"CANCELLED {side} {quantity} {symbol} (user declined)")
            return

    try:
        result = client.call_tool(order_tool, {
            "symbol": symbol, "side": side.lower(), "quantity": quantity,
        })
        print(f"Order result:\n{json.dumps(result, indent=2)[:2000]}")
        guards.record_trade(symbol, side, quantity, price)
        guards.log(f"EXECUTED {side} {quantity} {symbol} @ {price}")
    except MCPError as e:
        print(f"Order failed: {e}")
        guards.log(f"FAILED {side} {quantity} {symbol}: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    guards = Guardrails()
    client = get_client()
    cmd = sys.argv[1].lower()
    if cmd == "status":
        cmd_status(client, guards)
    elif cmd == "quote" and len(sys.argv) > 2:
        cmd_quote(client, guards, sys.argv[2])
    elif cmd in ("buy", "sell") and len(sys.argv) > 3:
        cmd_trade(client, guards, cmd, sys.argv[2], sys.argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
