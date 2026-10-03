# Robinhood Agent

An AI trading agent for **Robinhood Agentic Trading** — Robinhood's official program that lets third-party AI agents trade stocks, options, and crypto through a dedicated, ring-fenced account.

> ⚠️ **Risk warning:** Trading involves risk of loss, including total loss. You are solely responsible for every trade this agent places. Start with money you can afford to lose. This is not financial advice.

## How it works

1. You open a **dedicated Agentic Account** in Robinhood (separate from your main portfolio).
2. You connect this agent via Robinhood's official MCP server (`https://agent.robinhood.com/mcp/trading`) using OAuth.
3. The agent can only trade funds inside that account. Your main portfolio is walled off.
4. Every trade goes through local **guardrails** (position limits, daily loss cap, trade count cap, kill switch) before it ever reaches Robinhood.

## Prerequisites

- A U.S. Robinhood individual account in good standing
- Eligibility for Agentic Trading (rolling out in beta — Robinhood emails eligible users)
- Initial setup is **desktop-only** per Robinhood's docs
- Python 3.8+

## Setup

### 1. Open your Agentic Account
In the Robinhood app/website, open an Agentic Trading account and fund it with the amount you're willing to let the agent trade.

### 2. OAuth (get your token)
The MCP server uses OAuth 2.1:
1. Initiate auth from desktop per Robinhood's docs, complete verification in the Robinhood mobile app.
2. Save the resulting access token: `export ROBINHOOD_MCP_TOKEN="your-token"`

Tokens are never written to disk by this agent — only the env var.

### 3. Configure guardrails
Edit `guardrails.json` (created on first run from defaults). **`trading_enabled` defaults to `false`** — nothing trades until you flip it on.

### 4. Run
```bash
python3 agent.py status          # list tools, account, positions
python3 agent.py quote AAPL      # get a quote
python3 agent.py buy AAPL 10     # buy 10 shares (asks for approval)
python3 agent.py sell AAPL 5     # sell 5 shares
```

## Safety design

| Layer | What it does |
|---|---|
| Dedicated account | Agent can only touch funds you explicitly moved in |
| Kill switch | `trading_enabled: false` blocks everything |
| Position cap | No single position over `max_position_usd` |
| Daily loss halt | Stops trading if daily losses exceed the cap |
| Trade count cap | Max N trades per day |
| Approval mode | Every trade asks for confirmation (default ON) |
| Robinhood-side | Push notification per trade, activity feed, one-tap disconnect in the app |

## What this is NOT

- Not affiliated with Robinhood. Uses their official public MCP endpoint.
- Not financial advice. It executes *your* instructions within *your* limits.
- Not a strategy. It has no opinion on what to buy — that's your call.
