"""
Safety guardrails for the Robinhood trading agent — hardened.

Layers:
  1. Kill switch (trading_enabled) — default OFF
  2. Paper trading mode — simulate without real orders
  3. Symbol allow/blocklists
  4. Buy-only mode
  5. Per-position notional cap
  6. Daily trade count cap
  7. Daily loss halt
  8. Per-trade approval (default ON)
  9. Cooldown between trades on the same symbol
  10. Full audit log of every decision
"""
import json
import os
import time
from datetime import date, datetime

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guardrails.json")

DEFAULTS = {
    "trading_enabled": False,        # master kill switch — default OFF
    "paper_trading": True,           # simulate orders, never send real ones
    "require_approval": True,        # ask before every trade
    "max_position_usd": 100.0,
    "max_daily_loss_usd": 50.0,
    "max_daily_trades": 10,
    "min_seconds_between_trades": 60,  # cooldown per symbol
    "buy_only": False,
    "allowed_symbols": [],           # empty = all allowed
    "blocked_symbols": [],
    "universe": "sp500",             # sp500 | materials | sp500+materials | any
}


def load_universe():
    """Load S&P 500 + Materials universe from universe.json."""
    upath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe.json")
    try:
        with open(upath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sp500": [], "materials": []}


class Guardrails:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.base_dir = os.path.dirname(os.path.abspath(path))
        self.config = dict(DEFAULTS)
        if os.path.exists(path):
            with open(path) as f:
                loaded = json.load(f)
            # Only accept known keys — typo'd keys fail loudly
            unknown = set(loaded) - set(DEFAULTS)
            if unknown:
                raise ValueError(f"Unknown guardrail keys: {unknown}. Check {path}")
            self.config.update(loaded)
        self._trades_today = []
        self._last_trade_time = {}
        self._load_state()

    # ---- persistence ----
    def _state_path(self):
        return os.path.join(self.base_dir, ".agent_state.json")

    def _load_state(self):
        try:
            with open(self._state_path()) as f:
                state = json.load(f)
            if state.get("date") == str(date.today()):
                self._trades_today = state.get("trades", [])
                self._last_trade_time = state.get("last_trade_time", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self):
        tmp = self._state_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "date": str(date.today()),
                "trades": self._trades_today,
                "last_trade_time": self._last_trade_time,
            }, f)
        os.replace(tmp, self._state_path())  # atomic

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.config, f, indent=2)

    # ---- checks ----
    def check(self, symbol, side, quantity, price):
        """Returns (allowed: bool, reason: str). Pure — no side effects."""
        cfg = self.config
        symbol = symbol.upper()

        if not cfg["trading_enabled"]:
            return False, "Kill switch is OFF."
        if quantity <= 0:
            return False, f"Invalid quantity {quantity}."
        if price <= 0:
            return False, "No valid price — refusing to trade blind."
        if symbol in [s.upper() for s in cfg["blocked_symbols"]]:
            return False, f"{symbol} is blocklisted."
        if cfg["allowed_symbols"] and symbol not in [s.upper() for s in cfg["allowed_symbols"]]:
            return False, f"{symbol} not on allowlist."
        if cfg["buy_only"] and side.lower() in ("sell", "short"):
            return False, "Buy-only mode — sells rejected."

        # Universe restriction (S&P 500 / Materials)
        universe_mode = cfg.get("universe", "any")
        if universe_mode != "any":
            uni = load_universe()
            allowed = set()
            if "sp500" in universe_mode:
                allowed.update(s.upper() for s in uni.get("sp500", []))
            if "materials" in universe_mode:
                allowed.update(s.upper() for s in uni.get("materials", []))
            if allowed and symbol not in allowed:
                return False, f"{symbol} not in {universe_mode} universe."

        notional = quantity * price
        if notional > cfg["max_position_usd"]:
            return False, f"${notional:.2f} exceeds max position ${cfg['max_position_usd']:.2f}."

        today = str(date.today())
        todays = [t for t in self._trades_today if t["date"] == today]
        if len(todays) >= cfg["max_daily_trades"]:
            return False, f"Daily trade limit ({cfg['max_daily_trades']}) reached."

        daily_pnl = sum(t.get("pnl", 0) for t in todays)
        if daily_pnl <= -cfg["max_daily_loss_usd"]:
            return False, f"Daily loss limit hit (${daily_pnl:.2f}). Halted for today."

        last = self._last_trade_time.get(symbol, 0)
        cooldown = cfg["min_seconds_between_trades"]
        if time.time() - last < cooldown:
            wait = int(cooldown - (time.time() - last))
            return False, f"Cooldown: wait {wait}s before trading {symbol} again."

        return True, "OK"

    def record_trade(self, symbol, side, quantity, price, order_id=None, paper=False):
        symbol = symbol.upper()
        self._trades_today.append({
            "date": str(date.today()),
            "time": datetime.now().isoformat(),
            "symbol": symbol, "side": side,
            "quantity": quantity, "price": price,
            "order_id": order_id, "pnl": 0,
            "paper": paper,
        })
        self._last_trade_time[symbol] = time.time()
        self._save_state()

    def record_pnl(self, pnl):
        """Attach realized P&L to the most recent trade (for daily loss tracking)."""
        if self._trades_today:
            self._trades_today[-1]["pnl"] = pnl
            self._save_state()

    def daily_summary(self):
        today = str(date.today())
        todays = [t for t in self._trades_today if t["date"] == today]
        return {
            "trades": len(todays),
            "realized_pnl": round(sum(t.get("pnl", 0) for t in todays), 2),
            "paper_trades": sum(1 for t in todays if t.get("paper")),
            "live_trades": sum(1 for t in todays if not t.get("paper")),
        }

    def log(self, message):
        line = f"{datetime.now().isoformat()} {message}\n"
        with open(os.path.join(self.base_dir, "agent.log"), "a") as f:
            f.write(line)
        print(line.strip())
