"""
Investment thesis generator — one page on risk and reward per trade.

Every proposed trade gets a written thesis BEFORE execution:
  - The setup: what, how much, at what price
  - Bull case: upside drivers, catalysts, price target
  - Bear case: downside drivers, risks, stop level
  - Risk/reward math: explicit ratio
  - Why now: timing and catalysts
  - Position sizing: why this size
  - Invalidation: what would prove the thesis wrong
  - Verdict: justified or not, with a confidence score

Theses are saved to theses/ as markdown — a permanent audit trail.
"""
import json
import os
from datetime import datetime

THESIS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theses")


def build_thesis(symbol, side, quantity, price, context):
    """
    Build a one-page investment thesis.

    context: dict with any of:
      - quote: dict of quote data (change %, volume, etc.)
      - news: list of recent headlines
      - sector: GICS sector
      - reason: why the trade was proposed (strategy signal, user request, etc.)
      - target_price: bull-case target
      - stop_price: bear-case stop
      - universe_note: e.g. "S&P 500 Materials"
    Returns (markdown_text, verdict_dict).
    """
    symbol = symbol.upper()
    notional = quantity * price
    ctx = context or {}
    quote = ctx.get("quote", {})
    news = ctx.get("news", [])
    sector = ctx.get("sector", "Unknown")
    reason = ctx.get("reason", "Manual trade request")
    target = ctx.get("target_price")
    stop = ctx.get("stop_price")
    universe_note = ctx.get("universe_note", "")

    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append(f"# Investment Thesis: {side.upper()} {quantity} {symbol}")
    lines.append(f"*{now} — auto-generated, one page*")
    lines.append("")
    lines.append("## The Trade")
    lines.append(f"- **Action:** {side.upper()} {quantity} shares of {symbol} @ ~${price:.2f}")
    lines.append(f"- **Notional:** ${notional:.2f}")
    lines.append(f"- **Sector:** {sector}" + (f" ({universe_note})" if universe_note else ""))
    lines.append(f"- **Proposed because:** {reason}")
    if isinstance(quote, dict) and quote:
        chg = quote.get("change_pct", quote.get("change", "?"))
        vol = quote.get("volume", "?")
        lines.append(f"- **Market snapshot:** change {chg}%, volume {vol}")
    lines.append("")

    lines.append("## Bull Case (Reward)")
    lines.append("- Upside drivers: sector momentum, earnings trajectory, analyst sentiment,")
    lines.append("  macro tailwinds (rate environment, commodity prices for Materials names).")
    lines.append("- Catalysts to watch: upcoming earnings, guidance revisions, sector rotation,")
    lines.append("  M&A activity, commodity price moves.")
    if target:
        upside = (target - price) / price * 100 if price > 0 else 0
        lines.append(f"- **Price target:** ${target:.2f} ({upside:+.1f}% from entry)")
    else:
        lines.append("- **Price target:** not set — define one before sizing up.")
    lines.append("")

    lines.append("## Bear Case (Risk)")
    lines.append("- Downside drivers: earnings miss, guidance cut, sector rotation away,")
    lines.append("  macro headwinds (rising rates, demand slowdown, commodity price drops).")
    lines.append("- Company-specific: leverage, margin compression, regulatory exposure.")
    if stop:
        downside = (price - stop) / price * 100 if price > 0 else 0
        lines.append(f"- **Stop level:** ${stop:.2f} ({downside:.1f}% risk from entry)")
    else:
        lines.append("- **Stop level:** not set — every trade needs an exit plan.")
    lines.append("")

    lines.append("## Risk / Reward")
    if target and stop and price > 0 and (price - stop) > 0:
        reward = target - price
        risk = price - stop
        ratio = reward / risk
        lines.append(f"- Reward: ${reward:.2f}/share → Risk: ${risk:.2f}/share")
        lines.append(f"- **Ratio: {ratio:.2f}:1**")
        if ratio >= 2:
            lines.append("- Verdict on ratio: acceptable (≥ 2:1).")
        else:
            lines.append("- Verdict on ratio: WEAK (< 2:1) — reconsider or tighten stop.")
    else:
        lines.append("- Cannot compute ratio: target and stop must both be set.")
        lines.append("- Rule: no target + no stop = no trade.")
    lines.append("")

    lines.append("## Why Now")
    if news:
        lines.append("Recent headlines:")
        for n in news[:5]:
            lines.append(f"- {n}")
    else:
        lines.append("- No fresh headlines pulled — check earnings calendar and sector news")
        lines.append("  before committing capital.")
    lines.append("")

    lines.append("## Position Sizing")
    lines.append(f"- ${notional:.2f} notional keeps single-trade exposure small relative to")
    lines.append("  account guardrails (max position cap, daily loss halt).")
    lines.append("- Sizing rule: no single trade should risk more than the daily loss")
    lines.append("  limit can absorb. Small, ring-fenced, replaceable.")
    lines.append("")

    lines.append("## Invalidation")
    lines.append("- This thesis is WRONG if: price breaks below the stop level on volume,")
    lines.append("  the catalyst fails to materialize, or sector leadership rotates away.")
    lines.append("- Action on invalidation: exit, log the lesson, do not average down")
    lines.append("  without a new thesis.")
    lines.append("")

    # Verdict
    has_ratio = bool(target and stop and price > 0 and (price - stop) > 0)
    ratio_ok = has_ratio and ((target - price) / (price - stop)) >= 2
    justified = has_ratio and ratio_ok
    confidence = "medium" if justified else "low"

    lines.append("## Verdict")
    if justified:
        lines.append(f"- **JUSTIFIED** — risk/reward ≥ 2:1, stop and target defined.")
        lines.append(f"- Confidence: {confidence}. Proceed to guardrails + approval.")
    else:
        lines.append("- **NOT JUSTIFIED YET** — missing target, stop, or ratio < 2:1.")
        lines.append("- Fix the thesis first. A trade without a written edge is a gamble.")
    lines.append("")
    lines.append("---")
    lines.append("*This thesis is a reasoning aid, not financial advice. "
                 "The user is responsible for all trading decisions and losses.*")

    text = "\n".join(lines)
    verdict = {
        "justified": justified,
        "confidence": confidence,
        "risk_reward_ratio": round((target - price) / (price - stop), 2) if has_ratio else None,
    }
    return text, verdict


def save_thesis(symbol, side, text):
    """Save thesis to theses/ and return the path."""
    os.makedirs(THESIS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"{ts}-{side.lower()}-{symbol.upper()}.md"
    path = os.path.join(THESIS_DIR, fname)
    with open(path, "w") as f:
        f.write(text)
    return path
