"""
Safety guardrails for the Robinhood trading agent.
Every trade goes through these checks. No exceptions.
"""
import json
import os
from datetime import date, datetime

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "guardrails.json")

DEFAULTS = {
    "max_position_usd": 100.0,       # max dollars in any single position
    "max_daily_loss_usd": 50.0,      # halt trading if daily loss exceeds this
    "max_daily_trades": 10,          # max trades per day
    "require_approval": True,        # require human approval per trade
    "allowed_symbols": [],           # empty = any symbol allowed; else allowlist
    "blocked_symbols": [],           # never trade these
    "trading_enabled": False,        # master kill switch — default OFF
    "buy_only": False,               # if True, reject all sells/shorts
}


class Guardrails:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.config = dict(DEFAULTS)
        if os.path.exists(path):
            with open(path) as f:
                self.config.update(json.load(f))
        self._trades_today = []
        self._load_state()

    def _state_path(self):
        return os.path.join(os.path.dirname(self.path), ".agent_state.json")

    def _load_state(self):
        try:
            with open(self._state_path()) as f:
                state = json.load(f)
            if state.get("date") == str(date.today()):
                self._trades_today = state.get("trades", [])
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_state(self):
        with open(self._state_path(), "w") as f:
            json.dump({"date": str(date.today()), "trades": self._trades_today}, f)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.config, f, indent=2)

    def check(self, symbol, side, quantity, price):
        """Returns (allowed: bool, reason: str)."""
        cfg = self.config
        if not cfg["trading_enabled"]:
            return False, "Kill switch is OFF — trading disabled."
        if cfg["blocked_symbols"] and symbol.upper() in [s.upper() for s in cfg["blocked_symbols"]]:
            return False, f"{symbol} is on the blocklist."
        if cfg["allowed_symbols"] and symbol.upper() not in [s.upper() for s in cfg["allowed_symbols"]]:
            return False, f"{symbol} is not on the allowlist."
        if cfg["buy_only"] and side.lower() in ("sell", "short"):
            return False, "Buy-only mode — sells rejected."
        notional = quantity * price
        if notional > cfg["max_position_usd"]:
            return False, f"${notional:.2f} exceeds max position ${cfg['max_position_usd']:.2f}."
        today_trades = [t for t in self._trades_today if t["date"] == str(date.today())]
        if len(today_trades) >= cfg["max_daily_trades"]:
            return False, f"Daily trade limit ({cfg['max_daily_trades']}) reached."
        daily_pnl = sum(t.get("pnl", 0) for t in today_trades)
        if daily_pnl < -cfg["max_daily_loss_usd"]:
            return False, f"Daily loss limit hit (${daily_pnl:.2f}). Trading halted today."
        return True, "OK"

    def record_trade(self, symbol, side, quantity, price, order_id=None):
        self._trades_today.append({
            "date": str(date.today()),
            "time": datetime.now().isoformat(),
            "symbol": symbol, "side": side,
            "quantity": quantity, "price": price,
            "order_id": order_id, "pnl": 0,
        })
        self._save_state()

    def log(self, message):
        line = f"{datetime.now().isoformat()} {message}\n"
        with open(os.path.join(os.path.dirname(self.path), "agent.log"), "a") as f:
            f.write(line)
        print(line.strip())
