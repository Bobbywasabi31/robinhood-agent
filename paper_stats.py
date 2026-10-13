#!/usr/bin/env python3
"""
Paper-journal stats report — read-only analysis.

Crunches the paper-trading journal into win rate, avg win/loss, open
exposure, drawdown, and annualized Sharpe, Sortino, and Calmar ratios.
The journal is the
append-only JSONL file
(paper_journal.jsonl) that Guardrails maintains next to the daily
.agent_state.json; merging both keeps stats across the daily roll.

Strictly read-only: pure functions over a trade list, no trading logic, no
network, no real-money paths. Nothing here can place or alter an order.

Trade dicts follow the guardrails.record_trade() schema:
  {"date", "time", "symbol", "side", "quantity", "price",
   "order_id", "pnl", "paper"}
"""
import csv
import io
import json
import os
import re
from datetime import date as _date, timedelta as _timedelta

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


def resolve_days_range(days):
    """Resolve a `stats --days N` value into (date_from, date_to) ISO strings.

    The window is the last N days ending today, both bounds inclusive:
    N=1 means today only, N=7 covers today and the six days before it.
    Accepts an int or a CLI string; anything that isn't a positive whole
    number raises ValueError.
    """
    s = "" if days is None else str(days).strip()
    if not re.fullmatch(r"[0-9]+", s):
        raise ValueError(f"not a positive whole number of days: {days!r}")
    n = int(s)
    if n < 1:
        raise ValueError(f"not a positive whole number of days: {days!r}")
    today = _date.today()
    return (today - _timedelta(days=n - 1)).isoformat(), today.isoformat()


def resolve_top_n(value):
    """Resolve a `stats --top N` value into a positive int.

    Accepts an int or a CLI string; anything that isn't a positive whole
    number raises ValueError.
    """
    s = "" if value is None else str(value).strip()
    if not re.fullmatch(r"[0-9]+", s):
        raise ValueError(f"not a positive whole number: {value!r}")
    n = int(s)
    if n < 1:
        raise ValueError(f"not a positive whole number: {value!r}")
    return n


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


def normalize_symbols(symbols):
    """Canonical symbol list from a --symbols value (str, list, or None).

    Upper-cased, de-duped, order preserved; None/blank -> [].
    """
    if symbols is None:
        return []
    if isinstance(symbols, (list, tuple)):
        parts = [str(s).strip().upper() for s in symbols]
    else:
        parts = [s.strip().upper() for s in str(symbols).split(",")]
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def filter_trades_by_symbols(trades, symbols=None):
    """Keep trades whose symbol is in `symbols` (str or list, case-insensitive).

    Returns (filtered, excluded): symbol-less trades can't match and count
    as excluded. No symbols -> passthrough.
    """
    wanted = set(normalize_symbols(symbols))
    if not wanted:
        return list(trades), 0
    kept, excluded = [], 0
    for t in trades:
        if (t.get("symbol") or "").upper() in wanted:
            kept.append(t)
        else:
            excluded += 1
    return kept, excluded


def _bucket_stats(trades, key_fn):
    """Shared day/month bucketing: trades, pnl, wins, losses per key."""
    buckets = {}
    for t in trades:
        key = key_fn(t)
        pnl = t.get("pnl") or 0
        row = buckets.setdefault(
            key, {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        row["trades"] += 1
        row["pnl"] = round(row["pnl"] + pnl, 2)
        if pnl > 0:
            row["wins"] += 1
        elif pnl < 0:
            row["losses"] += 1
    return buckets


def _month_key(trade):
    """Calendar-month bucket (YYYY-MM) for a trade; undated when no date."""
    d = _trade_date(trade)
    return d.strftime("%Y-%m") if d else "undated"


def prepare_trades(trades, date_from=None, date_to=None, symbols=None):
    """Apply the stats filters to a trade list: date range, then symbol list.

    Returns (trades, undated_excluded, symbol_excluded); the survivors are
    sorted by (date, time) — time alone is wrong once the journal spans
    days (task 32). compute_stats() and `stats --trades` share this so
    both see the same trade list; the function is pure, no I/O.
    """
    filtered, undated_excluded = filter_trades_by_date(
        trades, date_from, date_to)
    wanted = normalize_symbols(symbols)
    filtered, symbol_excluded = filter_trades_by_symbols(filtered, wanted)
    filtered = sorted(
        filtered, key=lambda t: (t.get("date") or "", t.get("time") or ""))
    return filtered, undated_excluded, symbol_excluded


def compute_stats(trades, date_from=None, date_to=None, symbols=None, top_n=None):
    """Crunch a trade list into a stats dict. Pure — no I/O."""
    trades, undated_excluded, symbol_excluded = prepare_trades(
        trades, date_from=date_from, date_to=date_to, symbols=symbols)
    wanted = normalize_symbols(symbols)
    n = len(trades)

    pnl_values = [t.get("pnl") or 0 for t in trades]
    realized = [p for p in pnl_values if p != 0]
    wins = [p for p in realized if p > 0]
    losses = [p for p in realized if p < 0]

    # Per-day realized P&L (the append-only journal spans days).
    by_day = _bucket_stats(trades, lambda t: (t.get("date") or "undated"))

    # Per-month realized P&L (one row per calendar month, YYYY-MM).
    by_month = _bucket_stats(trades, _month_key)

    # Annualized Sharpe ratio over the daily P&L series: mean(daily P&L)
    # / sample-stdev(daily P&L), scaled by sqrt(252) trading days. Summed
    # per day straight from the trades (valid dates only — undated or
    # unparseable dates can't join a time series, and _bucket_stats keys
    # don't validate). None when fewer than 2 dated trading days or
    # zero variance.
    daily_totals = {}
    for t in trades:
        d = _trade_date(t)
        if d is None:
            continue
        key = d.isoformat()
        daily_totals[key] = round(daily_totals.get(key, 0.0) + (t.get("pnl") or 0), 2)
    daily_pnls = [daily_totals[k] for k in sorted(daily_totals)]
    sharpe_ratio, sortino_ratio = None, None
    sharpe_days = sortino_days = len(daily_pnls)
    if len(daily_pnls) >= 2:
        mean = sum(daily_pnls) / len(daily_pnls)
        var = sum((p - mean) ** 2 for p in daily_pnls) / (len(daily_pnls) - 1)
        if var > 0:
            sharpe_ratio = round(mean / var ** 0.5 * 252 ** 0.5, 2)
        # Sortino: same numerator, but the denominator is the sample stdev
        # of the losing days only, so upside volatility isn't penalized.
        # Needs 2+ losing days with nonzero downside variance.
        down = [p for p in daily_pnls if p < 0]
        if len(down) >= 2:
            dmean = sum(down) / len(down)
            dvar = sum((p - dmean) ** 2 for p in down) / (len(down) - 1)
            if dvar > 0:
                sortino_ratio = round(mean / dvar ** 0.5 * 252 ** 0.5, 2)

    # Annualized Calmar ratio: annualized mean daily P&L (mean x 252) over
    # the max drawdown of the cumulative daily-P&L curve, on the same dated
    # daily series as Sharpe/Sortino (undated trades excluded). Positive
    # means the stream earns its worst drawdown back that many times per
    # year; negative means the stream is losing. None with fewer than 2
    # dated days or no drawdown at all (dd == 0 has no defined ratio).
    calmar_ratio = None
    calmar_days = len(daily_pnls)
    if len(daily_pnls) >= 2:
        cum, peak, dd = 0.0, 0.0, 0.0
        for p in daily_pnls:
            cum += p
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        if dd > 0:
            calmar_ratio = round(sum(daily_pnls) / len(daily_pnls) * 252 / dd, 2)

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

    # Per-symbol leaderboard (read-only display filter): keep the top N
    # gainers and the bottom N by realized P&L. P&L ties break on symbol
    # name so the cut is deterministic; a symbol in both halves is kept
    # once. by_symbol_omitted counts the symbols left out.
    by_symbol_omitted = 0
    if top_n is not None:
        if not isinstance(top_n, int) or top_n < 1:
            raise ValueError(f"--top must be a positive whole number, got {top_n!r}")
        gainers = sorted(by_symbol.items(),
                         key=lambda kv: (-kv[1]["pnl"], kv[0]))[:top_n]
        losers = sorted(by_symbol.items(),
                        key=lambda kv: (kv[1]["pnl"], kv[0]))[:top_n]
        keep = []
        for sym, _ in gainers + losers:
            if sym not in keep:
                keep.append(sym)
        by_symbol_omitted = len(by_symbol) - len(keep)
        by_symbol = {sym: by_symbol[sym] for sym in keep}

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

    # Drawdown on the cumulative realized-P&L curve (peak-to-trough, $),
    # plus the window it happened in (peak date -> trough date). First
    # occurrence wins when the same drawdown depth repeats.
    cumulative, peak, peak_date, max_dd = 0.0, 0.0, None, 0.0
    dd_start, dd_end = None, None
    for t, p in zip(trades, pnl_values):
        d = _trade_date(t)
        cumulative += p
        if cumulative > peak:
            peak, peak_date = cumulative, d
        dd = peak - cumulative
        if dd > max_dd:
            max_dd, dd_start, dd_end = dd, peak_date, d
    dd_days = (dd_end - dd_start).days if dd_start and dd_end else None

    # Best/worst single realized trades (symbol + date + pnl). First
    # occurrence wins when two trades share the same pnl.
    best_trade, worst_trade = None, None
    for t, p in zip(trades, pnl_values):
        if p == 0:
            continue
        sym = (t.get("symbol") or "UNKNOWN").upper()
        day = _trade_date(t)
        candidate = {"symbol": sym,
                     "date": day.isoformat() if day else None,
                     "pnl": round(p, 2)}
        if best_trade is None or p > best_trade["pnl"]:
            best_trade = candidate
        if worst_trade is None or p < worst_trade["pnl"]:
            worst_trade = candidate

    gross_win = round(sum(wins), 2)
    gross_loss = round(-sum(losses), 2)
    denom = len(wins) + len(losses)

    # Win/loss streaks over realized trades in chronological order. Only
    # settled trades (pnl != 0) count: a flat trade neither advances nor
    # breaks a streak.
    longest_win, longest_loss, cur_win, cur_loss = 0, 0, 0, 0
    for p in pnl_values:
        if p > 0:
            cur_win += 1
            cur_loss = 0
            longest_win = max(longest_win, cur_win)
        elif p < 0:
            cur_loss += 1
            cur_win = 0
            longest_loss = max(longest_loss, cur_loss)
    current_streak = (
        {"type": "win", "length": cur_win} if cur_win
        else {"type": "loss", "length": cur_loss} if cur_loss
        else None
    )

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
        "by_month": by_month,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_start": dd_start.isoformat() if dd_start else None,
        "max_drawdown_end": dd_end.isoformat() if dd_end else None,
        "max_drawdown_days": dd_days,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "sharpe_ratio": sharpe_ratio,
        "sharpe_days": sharpe_days,
        "sortino_ratio": sortino_ratio,
        "sortino_days": sortino_days,
        "calmar_ratio": calmar_ratio,
        "calmar_days": calmar_days,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak": current_streak,
        "date_from": date_from,
        "date_to": date_to,
        "undated_excluded": undated_excluded,
        "symbols": wanted,
        "symbol_excluded": symbol_excluded,
        "top_n": top_n,
        "by_symbol_omitted": by_symbol_omitted,
    }


def _symbol_order(stats):
    """Symbol display order for the per-symbol section.

    Plain stats list symbols alphabetically; with --top N the section is a
    leaderboard, so symbols come best-to-worst by realized P&L (ties break
    on symbol name, mirroring the compute_stats cut).
    """
    syms = list(stats["by_symbol"])
    if stats.get("top_n"):
        syms.sort(key=lambda s: (-stats["by_symbol"][s]["pnl"], s))
    else:
        syms.sort()
    return syms


def format_report(stats):
    """Render a stats dict as a short human-readable report."""
    def money(v):
        return "n/a" if v is None else f"${v:,.2f}"

    def extreme_line(label, trade):
        if trade is None:
            return f"  {label:<20}n/a"
        day = trade["date"] or "undated"
        return f"  {label:<20}{money(trade['pnl'])} ({trade['symbol']}, {day})"

    def streak_str(streak):
        if streak is None:
            return "n/a"
        letter = {"win": "W", "loss": "L"}[streak["type"]]
        return f"{letter}{streak['length']}"

    win_rate = "n/a" if stats["win_rate"] is None else f"{stats['win_rate'] * 100:.1f}%"
    profit_factor = "n/a" if stats["profit_factor"] is None else str(stats["profit_factor"])
    sharpe = "n/a" if stats["sharpe_ratio"] is None else f"{stats['sharpe_ratio']:.2f}"
    sortino = "n/a" if stats["sortino_ratio"] is None else f"{stats['sortino_ratio']:.2f}"
    calmar = "n/a" if stats["calmar_ratio"] is None else f"{stats['calmar_ratio']:.2f}"
    dd_line = f"  Max drawdown:       {money(stats['max_drawdown'])}"
    dd_from, dd_to = stats.get("max_drawdown_start"), stats.get("max_drawdown_end")
    if dd_from and dd_to:
        dd_line += f" ({dd_from} -> {dd_to}"
        if stats.get("max_drawdown_days") is not None:
            dd_line += f", {stats['max_drawdown_days']}d"
        dd_line += ")"
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
    if stats.get("symbols"):
        note = ""
        if stats.get("symbol_excluded"):
            note = f" ({stats['symbol_excluded']} other-symbol trade(s) excluded)"
        lines.append(f"  Symbols:            {', '.join(stats['symbols'])}{note}")
    lines += [
        f"  Trades analyzed:    {stats['trades']}",
        f"  Realized (pnl set): {stats['realized_trades']} "
        f"({stats['wins']}W / {stats['losses']}L)",
        f"  Win rate:           {win_rate}",
        f"  Avg win:            {money(stats['avg_win'])}",
        f"  Avg loss:           {money(stats['avg_loss'])}",
        f"  Profit factor:      {profit_factor}",
        f"  {'Sharpe ratio (ann):':<20}{sharpe}",
        f"  {'Sortino ratio (ann):':<20}{sortino}",
        f"  {'Calmar ratio (ann):':<20}{calmar}",
        extreme_line("Best trade:", stats["best_trade"]),
        extreme_line("Worst trade:", stats["worst_trade"]),
        f"  Longest win streak: W{stats['longest_win_streak']}",
        f"  Longest loss streak:L{stats['longest_loss_streak']}",
        f"  Current streak:     {streak_str(stats['current_streak'])}",
        f"  Total P&L:          {money(stats['total_pnl'])}",
        dd_line,
        f"  Gross notional:     {money(stats['gross_notional'])}",
        f"  Open exposure:      {money(stats['open_exposure'])}",
    ]
    if stats["by_month"]:
        lines.append("  Monthly P&L:")
        for month in sorted(stats["by_month"]):
            row = stats["by_month"][month]
            wl = f"({row['wins']}W/{row['losses']}L)"
            lines.append(f"    {month}: {money(row['pnl'])} over {row['trades']} trades {wl}")
    if stats["by_day"]:
        lines.append("  Daily P&L:")
        for day in sorted(stats["by_day"]):
            row = stats["by_day"][day]
            wl = f"({row['wins']}W/{row['losses']}L)"
            lines.append(f"    {day}: {money(row['pnl'])} over {row['trades']} trades {wl}")
    if stats["by_symbol"]:
        if stats.get("top_n"):
            n = stats["top_n"]
            lines.append(f"  Per-symbol P&L (top {n} + bottom {n} by realized P&L):")
        else:
            lines.append("  Per-symbol P&L:")
        for sym in _symbol_order(stats):
            row = stats["by_symbol"][sym]
            wl = f"({row['wins']}W/{row['losses']}L)"
            lines.append(f"    {sym}: {money(row['pnl'])} over {row['trades']} trades {wl}")
        if stats.get("by_symbol_omitted"):
            lines.append(f"    ... {stats['by_symbol_omitted']} symbol(s) "
                         f"between top/bottom {stats['top_n']} not shown")
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


def format_csv(stats):
    """Render the tabular parts of a stats dict as CSV (spreadsheets/pandas).

    Columns: section,key,trades,wins,losses,pnl. Money is plain decimal
    numbers (no $ or thousands separators) so the file parses cleanly.
    Sections: summary (overall totals), best/worst (the extreme single
    trades), streak (longest win/loss streaks and the current one),
    risk (annualized Sharpe/Sortino/Calmar ratios; trades = dated trading
    days used, pnl = the ratio — no $ involved), day (one row per day, sorted), month (one row
    per calendar month, sorted), symbol (one row per ticker, sorted).
    Empty breakdowns produce no rows, never an error.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "trades", "wins", "losses", "pnl"])
    writer.writerow(["summary", "ALL", stats["trades"], stats["wins"],
                     stats["losses"], f"{stats['total_pnl']:.2f}"])
    for section, trade in (("best", stats["best_trade"]),
                           ("worst", stats["worst_trade"])):
        if trade:
            day = trade["date"] or "undated"
            pnl = trade["pnl"]
            writer.writerow([section, f"{trade['symbol']} {day}", 1,
                             1 if pnl > 0 else 0, 1 if pnl < 0 else 0,
                             f"{pnl:.2f}"])
    lw, ll = stats["longest_win_streak"], stats["longest_loss_streak"]
    writer.writerow(["streak", "longest_win", lw, lw, 0, ""])
    writer.writerow(["streak", "longest_loss", ll, 0, ll, ""])
    cur = stats["current_streak"]
    if cur:
        w = cur["length"] if cur["type"] == "win" else 0
        writer.writerow(["streak", f"current_{cur['type']}", cur["length"],
                         w, cur["length"] - w, ""])
    if stats["sharpe_ratio"] is not None:
        writer.writerow(["risk", "sharpe_ratio_ann", stats["sharpe_days"],
                         "", "", f"{stats['sharpe_ratio']:.2f}"])
    if stats["sortino_ratio"] is not None:
        writer.writerow(["risk", "sortino_ratio_ann", stats["sortino_days"],
                         "", "", f"{stats['sortino_ratio']:.2f}"])
    if stats["calmar_ratio"] is not None:
        writer.writerow(["risk", "calmar_ratio_ann", stats["calmar_days"],
                         "", "", f"{stats['calmar_ratio']:.2f}"])
    for day in sorted(stats["by_day"]):
        row = stats["by_day"][day]
        writer.writerow(["day", day, row["trades"], row["wins"],
                         row["losses"], f"{row['pnl']:.2f}"])
    for month in sorted(stats["by_month"]):
        row = stats["by_month"][month]
        writer.writerow(["month", month, row["trades"], row["wins"],
                         row["losses"], f"{row['pnl']:.2f}"])
    for sym in _symbol_order(stats):
        row = stats["by_symbol"][sym]
        writer.writerow(["symbol", sym, row["trades"], row["wins"],
                         row["losses"], f"{row['pnl']:.2f}"])
    return buf.getvalue()


def format_trades_csv(trades):
    """Render a trade list as CSV, one row per trade (spreadsheets/pandas).

    Columns: date,time,symbol,side,quantity,price,realized_pnl. Money is
    plain decimal numbers (no $ or thousands separators) so the file parses
    cleanly; missing fields render as empty cells. The rows mirror
    prepare_trades() output order (date, then time). An empty list yields
    the header alone, never an error.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["date", "time", "symbol", "side", "quantity", "price", "realized_pnl"])
    for t in trades:
        price = t.get("price")
        pnl = t.get("pnl")
        qty = t.get("quantity")
        writer.writerow([
            t.get("date") or "",
            t.get("time") or "",
            (t.get("symbol") or "").upper(),
            t.get("side") or "",
            qty if qty is not None else "",
            f"{price:.2f}" if price is not None else "",
            f"{pnl:.2f}" if pnl is not None else "",
        ])
    return buf.getvalue()
