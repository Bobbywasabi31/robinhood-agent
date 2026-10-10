#!/usr/bin/env python3
"""
Paper-journal stats report — read-only analysis.

Crunches the paper-trading journal into win rate, avg win/loss, open
exposure, and drawdown. The journal is the append-only JSONL file
(paper_journal.jsonl) that Guardrails maintains next to the daily
.agent_state.json; merging both keeps stats across the daily roll.

Strictly read-only: pure functions over a trade list, no trading logic, no
network, no real-money paths. Nothing here can place or alter an order.

Trade dicts follow the guardrails.record_trade() schema:
  {"date", "time", "symbol", "side", "quantity", "price",
   "order_id", "pnl", "paper"}
"""
import json
import os
from datetime import date as _date

APPEND_JOURNAL_FILENAME = "paper_journal.jsonl"

# Mirrors guardrails.Guardrails._state_path() — read here, never written.
JOURNAL_FILENAME = ".agent_state.json"


def load_journal(state_path):
    """Read the persisted journal; missing/corrupt file -> []."""
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    trades = state.get("trades", [])
    return trades if isinstance(trades, list) else []


def default_journal_path(repo_dir=None):
    repo_dir = repo_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(repo_dir, JOURNAL_FILENAME)


def default_append_journal_path(repo_dir=None):
    repo_dir = repo_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(repo_dir, APPEND_JOURNAL_FILENAME)


def load_append_journal(path):
    """Read the append-only JSONL journal; missing file or bad lines -> skipped."""
    trades = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    trades.append(entry)
    except (FileNotFoundError, OSError):
        return []
    return trades


def trade_key(trade):
    """Identity of a trade for dedupe — stable across the journal and the state file."""
    return (
        str(trade.get("date")), str(trade.get("time")), str(trade.get("symbol")),
        str(trade.get("side")), trade.get("quantity"), trade.get("price"),
        str(trade.get("order_id")),
    )


def merge_trades(*trade_lists):
    """Union of trade lists in order, dropping exact duplicates (first copy wins)."""
    seen, merged = set(), []
    for trades in trade_lists:
        for t in trades:
            key = trade_key(t)
            if key not in seen:
                seen.add(key)
                merged.append(t)
    return merged


def parse_iso_date(value):
    """Parse a YYYY-MM-DD date string; blank/None -> None; garbage -> ValueError."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return _date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"not a YYYY-MM-DD date: {value!r}")


def _trade_date(trade):
    """A trade's date, or None when missing/unparseable (never raises)."""
    try:
        return parse_iso_date(trade.get("date"))
    except ValueError:
        return None


def filter_trades_by_date(trades, date_from=None, date_to=None):
    """Keep trades with date in [date_from, date_to] (inclusive, ISO strings).

    Returns (filtered, undated_excluded): trades with a missing or
    unparseable date are dropped only when a bound is actually set.
    A bound that isn't YYYY-MM-DD, or from > to, raises ValueError.
    """
    d_from = parse_iso_date(date_from) if date_from else None
    d_to = parse_iso_date(date_to) if date_to else None
    if d_from is not None and d_to is not None and d_from > d_to:
        raise ValueError(f"--from {date_from} is after --to {date_to}")
    if d_from is None and d_to is None:
        return list(trades), 0
    kept, undated = [], 0
    for t in trades:
        d = _trade_date(t)
        if d is None:
            undated += 1
            continue
        if d_from is not None and d < d_from:
            continue
        if d_to is not None and d > d_to:
            continue
        kept.append(t)
    return kept, undated


def compute_stats(trades, date_from=None, date_to=None):
    """Crunch a trade list into a stats dict. Pure — no I/O."""
    trades, undated_excluded = filter_trades_by_date(trades, date_from, date_to)
    # Sort by (date, time): time alone is wrong once the journal spans days.
    trades = sorted(trades, key=lambda t: (t.get("date") or "", t.get("time") or ""))
    n = len(trades)

    pnl_values = [t.get("pnl") or 0 for t in trades]
    realized = [p for p in pnl_values if p != 0]
    wins = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]

    # Per-day realized P&L (the append-only journal spans days).
    by_day = {}
    for t in trades:
        day = t.get("date") or "undated"
        pnl = t.get("pnl") or 0
        row = by_day.setdefault(
            day, {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        row["trades"] += 1
        row["pnl"] = round(row["pnl"] + pnl, 2)
        if pnl > 0:
            row["wins"] += 1
        elif pnl < 0:
            row["losses"] += 1

    # Per-symbol realized P&L (win/loss counts per ticker).
    by_symbol = {}
    for t in trades:
        sym = (t.get("symbol") or "UNKNOWN").upper()
        row = by_symbol.setdefault(
            sym, {"trades": 0, "realized": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        pnl = t.get("pnl") or 0
        row["trades"] += 1
        row["pnl"] = round(row["pnl"] + pnl, 2)
        if pnl != 0:
            row["realized"] += 1
            if pnl > 0:
                row["wins"] += 1
            else:
                row["losses"] += 1

    # Open exposure: net quantity per symbol at last traded price.
    net_qty, last_price = {}, {}
    for t in trades:
        sym = (t.get("symbol") or "").upper()
        qty = t.get("quantity") or 0
        price = t.get("price") or 0
        sign = -1 if str(t.get("side", "")).lower() in ("sell", "short") else 1
        net_qty[sym] = net_qty.get(sym, 0) + sign * qty
        last_price[sym] = price
    positions = {
        sym: round(abs(q) * last_price.get(sym, 0), 2)
        for sym, q in net_qty.items() if q != 0
    }

    # Drawdown on the cumulative realized-P&L curve (peak-to-trough, $).
    cumulative, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnl_values:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    gross_win = round(sum(wins), 2)
    gross_loss = round(-sum(losses), 2)
    denom = len(wins) + len(losses)
    return {
        "trades": n,
        "realized_trades": len(realized),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / denom, 3) if denom else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "total_pnl": round(sum(pnl_values), 2),
        "gross_notional": round(
            sum((t.get("quantity") or 0) * (t.get("price") or 0) for t in trades), 2
        ),
        "open_exposure": round(sum(positions.values()), 2),
        "positions": positions,
        "by_symbol": by_symbol,
        "by_day": by_day,
        "max_drawdown": round(max_dd, 2),
        "date_from": date_from,
        "date_to": date_to,
        "undated_excluded": undated_excluded,
    }


def format_report(stats):
    """Render a stats dict as a short human-readable report."""
    def money(v):
        return "n/a" if v is None else f"${v:,.2f}"

    win_rate = "n/a" if stats["win_rate"] is None else f"{stats['win_rate'] * 100:.1f}%"
    profit_factor = "n/a" if stats["profit_factor"] is None else str(stats["profit_factor"])
    lines = [
        "",
        "Paper journal stats",
    ]
    if stats.get("date_from") or stats.get("date_to"):
        rng = f"{stats['date_from'] or '...'} -> {stats['date_to'] or '...'}"
        note = ""
        if stats.get("undated_excluded"):
            note = f" ({stats['undated_excluded']} undated trade(s) excluded)"
        lines.append(f"  Date range:         {rng}{note}")
    lines += [
        f"  Trades analyzed:    {stats['trades']}",
        f"  Realized (pnl set): {stats['realized_trades']} "
        f"({stats['wins']}W / {stats['losses']}L)",
        f"  Win rate:           {win_rate}",
        f"  Avg win:            {money(stats['avg_win'])}",
        f"  Avg loss:           {money(stats['avg_loss'])}",
        f"  Profit factor:      {profit_factor}",
        f"  Total P&L:          {money(stats['total_pnl'])}",
        f"  Max drawdown:       {money(stats['max_drawdown'])}",
        f"  Gross notional:     {money(stats['gross_notional'])}",
        f"  Open exposure:      {money(stats['open_exposure'])}",
    ]
    if stats["by_day"]:
        lines.append("  Daily P&L:")
        for day in sorted(stats["by_day"]):
            row = stats["by_day"][day]
            wl = f"({row['wins']}W/{row['losses']}L)"
            lines.append(f"    {day}: {money(row['pnl'])} over {row['trades']} trades {wl}")
    if stats["by_symbol"]:
        lines.append("  Per-symbol P&L:")
        for sym in sorted(stats["by_symbol"]):
            row = stats["by_symbol"][sym]
            wl = f"({row['wins']}W/{row['losses']}L)"
            lines.append(f"    {sym}: {money(row['pnl'])} over {row['trades']} trades {wl}")
    if stats["positions"]:
        lines.append("  Open positions:")
        for sym in sorted(stats["positions"]):
            lines.append(f"    {sym}: {money(stats['positions'][sym])}")
    else:
        lines.append("  Open positions:     none")
    return "\n".join(lines) + "\n"


def format_json(stats, live_excluded=0):
    """Render a stats dict as machine-readable JSON (for other tools/AIs).

    Carries the same exclusion info as format_report(): the human report
    prints a note when live trades were dropped, here it's a count field.
    """
    payload = {"stats": stats, "live_excluded": live_excluded}
    return json.dumps(payload, indent=2, sort_keys=True)
