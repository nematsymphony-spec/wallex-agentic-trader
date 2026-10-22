from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .clients import OpenAIDecider, RemoteError, RuleDecider, WallexPublic
from .core import D, MarketView, Portfolio, RiskSettings, SYMBOLS, canonical_hash, risk_check, utc_now


class AuditLog:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS order_ids (client_id TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.commit()

    def append(self, kind: str, symbol: Optional[str], payload: dict) -> str:
        row = self.connection.execute("SELECT event_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else "GENESIS"
        record = {"at": utc_now(), "kind": kind, "symbol": symbol, "payload": payload, "prev_hash": prev}
        digest = canonical_hash(record)
        self.connection.execute("INSERT INTO events(at,kind,symbol,payload,prev_hash,event_hash) VALUES(?,?,?,?,?,?)", (record["at"], kind, symbol, json.dumps(payload, sort_keys=True, default=str), prev, digest))
        self.connection.commit()
        return digest

    def reserve_order(self, client_id: str, detail: dict) -> bool:
        try:
            self.connection.execute("INSERT INTO order_ids VALUES(?,?,?)", (client_id, "reserved", json.dumps(detail, default=str)))
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def order_state(self, client_id: str, state: str, detail: dict) -> None:
        self.connection.execute("UPDATE order_ids SET state=?, detail=? WHERE client_id=?", (state, json.dumps(detail, default=str), client_id))
        self.connection.commit()

    def pending_orders(self) -> List[str]:
        return [x[0] for x in self.connection.execute("SELECT client_id FROM order_ids WHERE state IN ('reserved','open','unknown','partial')")]

    def save_state(self, key: str, value: dict) -> None:
        self.connection.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value, default=str)))
        self.connection.commit()

    def load_state(self, key: str) -> Optional[dict]:
        row = self.connection.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def verify(self) -> bool:
        prev = "GENESIS"
        for at, kind, symbol, payload, stored_prev, digest in self.connection.execute("SELECT at,kind,symbol,payload,prev_hash,event_hash FROM events ORDER BY id"):
            if stored_prev != prev or digest != canonical_hash({"at": at, "kind": kind, "symbol": symbol, "payload": json.loads(payload), "prev_hash": prev}):
                return False
            prev = digest
        return True

    def export_events(self, limit: int = 0) -> List[dict]:
        query = "SELECT id,at,kind,symbol,payload,prev_hash,event_hash FROM events ORDER BY id"
        rows = self.connection.execute(query).fetchall()
        if limit > 0:
            rows = rows[-limit:]
        return [{"id": r[0], "at": r[1], "kind": r[2], "symbol": r[3], "payload": json.loads(r[4]), "prev_hash": r[5], "event_hash": r[6]} for r in rows]


def features(symbol: str, candles: List[dict], bid: Decimal, ask: Decimal, bid_depth: Decimal, ask_depth: Decimal, active: bool, allowed: bool) -> MarketView:
    candles = sorted(candles, key=lambda x: x["timestamp"])
    if len(candles) < 13 or len({x["timestamp"] for x in candles[-13:]}) != 13:
        raise ValueError("insufficient unique completed candles")
    latest = candles[-1]
    returns = [(candles[i]["close"] / candles[i - 1]["close"] - 1) for i in range(len(candles) - 12, len(candles))]
    mean = sum(returns, D(0)) / D(len(returns))
    variance = sum(((x - mean) ** 2 for x in returns), D(0)) / D(len(returns))
    vol_quote = sum((x["volume"] * x["close"] for x in candles[-12:]), D(0))
    return MarketView(symbol, latest["timestamp"], latest["close"], bid, ask, bid_depth, ask_depth, latest["close"] / candles[-4]["close"] - 1, latest["close"] / candles[-13]["close"] - 1, variance.sqrt(), vol_quote, active, allowed)


def book_levels(book: dict) -> tuple:
    bids = [(D(str(x["price"])), D(str(x["quantity"]))) for x in book["bid"][:5]]
    asks = [(D(str(x["price"])), D(str(x["quantity"]))) for x in book["ask"][:5]]
    if not bids or not asks or bids[0][0] <= 0 or asks[0][0] <= bids[0][0]:
        raise ValueError("invalid book")
    return bids[0][0], asks[0][0], sum((p * q for p, q in bids), D(0)), sum((p * q for p, q in asks), D(0))


class Engine:
    def __init__(self, decider, portfolio: Portfolio, log: AuditLog, settings: RiskSettings = None):
        self.decider, self.portfolio, self.log = decider, portfolio, log
        self.settings = settings or portfolio.settings
        self.prices: Dict[str, Decimal] = {}

    def step(self, view: MarketView, mode: str = "shadow", simulated_fill_price: Optional[Decimal] = None) -> dict:
        self.prices[view.symbol] = view.bid  # Conservative liquidation mark.
        equity = self.portfolio.mark(view.timestamp, self.prices)
        snapshot = view.model_input()
        snapshot_hash = self.log.append("market_snapshot", view.symbol, snapshot)
        decision = self.decider.decide(view, view.symbol in self.portfolio.positions)
        decision_data = asdict(decision)
        decision_hash = self.log.append("decision", view.symbol, {"snapshot_hash": snapshot_hash, "decision": decision_data})
        intent, reason = risk_check(decision, view, self.portfolio, self.settings, self.prices)
        self.log.append("risk", view.symbol, {"decision_event": decision_hash, "approved": intent is not None, "reason": reason, "equity_tmn": str(equity), "daily_stop": self.portfolio.daily_stop, "total_stop": self.portfolio.total_stop})
        result = {"symbol": view.symbol, "timestamp": view.timestamp, "action": decision.action, "risk": reason, "equity_tmn": str(equity), "mode": mode}
        if intent is None:
            return result
        if not self.log.reserve_order(intent.client_id, asdict(intent)):
            self.log.append("order_duplicate_blocked", view.symbol, {"client_id": intent.client_id})
            result["risk"] = "duplicate_order"
            return result
        self.log.append("order_intent", view.symbol, {"decision_event": decision_hash, "intent": asdict(intent), "mode": mode})
        result["client_id"] = intent.client_id
        if mode in ("backtest", "shadow"):
            price = simulated_fill_price if simulated_fill_price is not None else intent.limit_price
            quantity = intent.quantity
            if intent.side == "BUY":
                quantity = min(quantity, self.portfolio.cash / (price * (D(1) + self.settings.taker_fee_fraction)))
            fee = quantity * price * self.settings.taker_fee_fraction
            if quantity <= 0:
                self.log.order_state(intent.client_id, "rejected", {"reason": "insufficient_cash_after_gap"})
                return {**result, "fill": "rejected"}
            self.portfolio.apply_fill(intent, quantity, price, fee)
            self.log.order_state(intent.client_id, "filled", {"quantity": str(quantity), "price": str(price), "fee": str(fee)})
            self.log.append("fill", view.symbol, {"client_id": intent.client_id, "quantity": str(quantity), "price": str(price), "fee_tmn": str(fee), "model": "synthetic next-open" if mode == "backtest" else "shadow at live bid/ask"})
            result["fill"] = "simulated"
        else:
            raise ValueError("live execution unavailable")
        return result


def collect_live_views(api: WallexPublic, package_id: int, symbols=SYMBOLS) -> List[MarketView]:
    markets = api.markets()
    unavailable = api.unavailable(package_id)
    now = int(time.time())
    last_complete_hour = now // 3600 * 3600 - 3600
    views = []
    for symbol in symbols:
        market = markets.get(symbol, {})
        if not market or symbol in unavailable or not market.get("spot_active"):
            continue
        candles = [x for x in api.candles(symbol, last_complete_hour - 16 * 3600, last_complete_hour + 3600) if x["timestamp"] <= last_complete_hour]
        if not candles or candles[-1]["timestamp"] != last_complete_hour:
            raise RemoteError("stale candle data")
        bid, ask, bid_depth, ask_depth = book_levels(api.depth(symbol))
        views.append(features(symbol, candles, bid, ask, bid_depth, ask_depth, True, True))
    return views


def run_shadow(db: str, package_id: int, model: str = "openai") -> List[dict]:
    api = WallexPublic()
    log = AuditLog(db)
    settings = RiskSettings()
    portfolio = Portfolio(settings)
    saved = log.load_state("shadow_portfolio")
    if saved:
        from .core import Position
        portfolio.cash = D(saved["cash"])
        portfolio.positions = {s: Position(s, D(p["quantity"]), D(p["entry_cost_tmn"])) for s, p in saved["positions"].items()}
        portfolio.peak_equity = D(saved["peak_equity"])
        portfolio.day_start_equity = D(saved["day_start_equity"])
        portfolio.day = __import__("datetime").date.fromisoformat(saved["day"]) if saved["day"] else None
        portfolio.total_stop = saved["total_stop"]
        portfolio.daily_stop = saved["daily_stop"]
    engine = Engine(OpenAIDecider() if model == "openai" else RuleDecider(), portfolio, log)
    results = []
    try:
        views = collect_live_views(api, package_id)
        if any(symbol not in {v.symbol for v in views} for symbol in portfolio.positions):
            raise RemoteError("open position has no fresh market view")
        engine.prices = {v.symbol: v.bid for v in views}
        for view in views:
            results.append(engine.step(view, "shadow"))
            log.save_state("shadow_portfolio", {"cash": str(portfolio.cash), "positions": {s: asdict(p) for s, p in portfolio.positions.items()}, "peak_equity": str(portfolio.peak_equity), "day_start_equity": str(portfolio.day_start_equity), "day": str(portfolio.day) if portfolio.day else None, "total_stop": portfolio.total_stop, "daily_stop": portfolio.daily_stop})
    except (RemoteError, ValueError, KeyError) as exc:
        log.append("cycle_error", None, {"error_type": type(exc).__name__, "action": "no_new_orders"})
        raise
    return results


def run_backtest(db: str, start: int, end: int, model: str = "rule", spread_bps: Decimal = D(20), package_id: int = 183) -> dict:
    if model not in ("rule", "openai") or start >= end or spread_bps <= 0:
        raise ValueError("invalid backtest inputs")
    api = WallexPublic()
    markets, unavailable = api.markets(), api.unavailable(package_id)
    histories = {s: api.candles(s, start - 13 * 3600, end) for s in SYMBOLS if s in markets and s not in unavailable and markets[s].get("spot_active")}
    log = AuditLog(db)
    settings = RiskSettings()
    portfolio = Portfolio(settings)
    engine = Engine(OpenAIDecider() if model == "openai" else RuleDecider(), portfolio, log)
    rows_by_time: Dict[int, list] = {}
    for symbol, rows in histories.items():
        for i in range(13, len(rows)):
            next_bar = rows[i]
            if start <= next_bar["timestamp"] < end:
                rows_by_time.setdefault(next_bar["timestamp"], []).append((symbol, rows[:i], next_bar))
    equity_curve = []
    decisions = 0
    for timestamp, items in sorted(rows_by_time.items()):
        engine.prices.update({symbol: seen[-1]["close"] * (D(1) - spread_bps / D(20000)) for symbol, seen, _ in items})
        for symbol, seen, next_bar in items:
            c = seen[-1]["close"]
            bid = c * (D(1) - spread_bps / D(20000))
            ask = c * (D(1) + spread_bps / D(20000))
            # Historical depth is unavailable: assume five times 20% of starting equity.
            depth = settings.starting_equity
            view = features(symbol, seen, bid, ask, depth, depth, True, True)
            fill_price = next_bar["open"] * (D(1) + spread_bps / D(20000)) if symbol not in portfolio.positions else next_bar["open"] * (D(1) - spread_bps / D(20000))
            engine.step(view, "backtest", simulated_fill_price=fill_price)
            decisions += 1
        equity_curve.append({"timestamp": timestamp, "equity_tmn": str(portfolio.equity(engine.prices))})
    if not equity_curve:
        raise ValueError("no completed bars in requested interval")
    final = D(equity_curve[-1]["equity_tmn"])
    peak = settings.starting_equity
    max_drawdown = D(0)
    for point in equity_curve:
        equity = D(point["equity_tmn"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    summary = {"kind": "historical_simulation", "decision_model": model, "assumed_spread_bps": str(spread_bps), "fee_fraction_per_side": str(settings.taker_fee_fraction), "start": start, "end": end, "decisions": decisions, "start_equity_tmn": str(settings.starting_equity), "final_equity_tmn": str(final), "net_return_pct": str((final / settings.starting_equity - 1) * 100), "max_drawdown_pct": str(max_drawdown * 100), "equity_curve": equity_curve, "limitations": ["Signals use completed bars and fill at next bar open", "No historical order-book depth; constant assumed spread and depth", "Full fills at synthetic next-open", "Rule model is an engineering baseline, not AI"] if model == "rule" else ["Signals use completed bars and fill at next bar open", "No historical order-book depth; constant assumed spread and depth", "Full fills at synthetic next-open"]}
    log.append("backtest_summary", None, summary)
    return summary
