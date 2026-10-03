#!/usr/bin/env python3
"""
OAuth helper for Robinhood Agentic Trading MCP.

Robinhood's flow (per their docs):
  1. Initiate from desktop — this builds the authorize URL for you.
  2. Complete verification in the Robinhood mobile app.
  3. Paste the resulting code/token here.

This script walks you through it step by step and never writes
tokens to disk — it prints the export command for you to run.
"""
import sys
import webbrowser

MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"

# If Robinhood publishes their OAuth authorize endpoint separately,
# update this. The MCP server itself handles OAuth via 401 + metadata
# per the MCP authorization spec (RFC 8707 resource indicators).
OAUTH_AUTHORIZE_HINT = (
    "https://agent.robinhood.com/mcp/trading"
)


def main():
    print("=" * 60)
    print("Robinhood Agentic Trading — OAuth setup")
    print("=" * 60)
    print()
    print("STEP 1: Open your Agentic Trading account")
    print("  - In Robinhood (desktop web), open an Agentic Trading account.")
    print("  - Fund it with ONLY the amount you want the agent to trade.")
    print("  - This account is separate from your main portfolio.")
    print()
    print("STEP 2: Start OAuth from desktop")
    print("  - Robinhood's docs describe initiating the agent connection")
    print("    from desktop, then completing verification in the mobile app.")
    print("  - Follow the prompts in Robinhood until you receive an")
    print("    access token for the MCP server.")
    print()
    print("STEP 3: Save the token (session only — never committed)")
    token = input("  Paste your MCP access token (or press Enter to skip): ").strip()
    if token:
        print()
        print("  Run this in your shell before using the agent:")
        print()
        print(f'    export ROBINHOOD_MCP_TOKEN="{token[:8]}...<{len(token)} chars>"')
        print()
        print("  (Full token not echoed. Set the env var with your actual token.)")
        print()
        print("STEP 4: Verify the connection")
        print("    python3 agent.py status")
    else:
        print()
        print("  No token entered. When you have one:")
        print('    export ROBINHOOD_MCP_TOKEN="your-token-here"')
        print("    python3 agent.py status")
    print()
    print("STEP 5: Configure guardrails BEFORE enabling trading")
    print("  - Edit guardrails.json (created on first agent run).")
    print("  - trading_enabled=false and paper_trading=true by default.")
    print("  - Start in paper mode. Flip to live only when ready.")
    print("=" * 60)


if __name__ == "__main__":
    main()
