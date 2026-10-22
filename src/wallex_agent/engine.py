from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .clients import OpenAIDecider, RemoteError, RuleDecider, WallexPublic
from .core import D, MarketView, Portfolio, RiskSettings, canonical_hash, risk_check, utc_now
from .selector import GlobalOpenAIDecider, GlobalRuleDecider, rank_views


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
    vol_quote = sum((x["volume"] * x["close"] for x in candles[-24:]), D(0))
    volatility_3d = D(0)
    if len(candles) >= 73 and all(candles[i]["timestamp"] - candles[i - 1]["timestamp"] == 3600 for i in range(len(candles) - 72, len(candles))):
        hourly = [candles[i]["close"] / candles[i - 1]["close"] - 1 for i in range(len(candles) - 72, len(candles))]
        windows = [sum((r * r for r in hourly[-12 * (j + 1):len(hourly) - 12 * j if j else None]), D(0)).sqrt() for j in range(6)]
        daily = [(windows[2 * j] + windows[2 * j + 1]) / 2 for j in range(3)]
        volatility_3d = daily[0] * D("0.5") + daily[1] * D("0.3") + daily[2] * D("0.2")
    return MarketView(symbol, latest["timestamp"], latest["close"], bid, ask, bid_depth, ask_depth, latest["close"] / candles[-4]["close"] - 1, latest["close"] / candles[-13]["close"] - 1, variance.sqrt(), vol_quote, active, allowed, volatility_3d)


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

    def step(self, view: MarketView, mode: str = "shadow", simulated_fill_price: Optional[Decimal] = None, decision=None, mark_timestamp: Optional[int] = None) -> dict:
        self.prices[view.symbol] = view.bid  # Conservative liquidation mark.
        equity = self.portfolio.mark(mark_timestamp if mark_timestamp is not None else view.timestamp, self.prices)
        snapshot = view.model_input()
        snapshot_hash = self.log.append("market_snapshot", view.symbol, snapshot)
        decision = decision or self.decider.decide(view, view.symbol in self.portfolio.positions)
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

    def cycle(self, views: List[MarketView], mode: str = "shadow", fill_prices: Optional[dict] = None, decision_timestamp: Optional[int] = None) -> dict:
        if not views:
            self.log.append("universe_empty", None, {"action": "HOLD"})
            return {"action": "HOLD", "symbol": "NONE", "reason": "no_fresh_markets"}
        timestamps = {v.timestamp for v in views}
        if len(timestamps) != 1:
            raise ValueError("mixed candle timestamps")
        timestamp = next(iter(timestamps))
        self.prices.update({v.symbol: v.bid for v in views})
        mark_time = decision_timestamp if decision_timestamp is not None else int(time.time())
        equity = self.portfolio.mark(mark_time, self.prices)
        ranked, reasons = rank_views(views, self.settings, timestamp)
        scan_hash = self.log.append("universe_scan", None, {"timestamp": timestamp, "equity_tmn": str(equity), "markets": [{"view": v.model_input(), "gate": reasons[v.symbol]} for v in sorted(views, key=lambda x: x.symbol)], "ranking": ranked})
        by_symbol = {v.symbol: v for v in views}
        selected_symbols = [x["symbol"] for x in ranked]
        for held in self.portfolio.positions:
            if held not in by_symbol:
                raise ValueError("held market missing from fresh scan")
            if held not in selected_symbols:
                selected_symbols.append(held)
        shortlist = [by_symbol[s] for s in selected_symbols]
        decision = self.decider.decide_all(shortlist, ranked, set(self.portfolio.positions))
        self.log.append("global_decision", decision.symbol if decision.symbol != "NONE" else None, {"scan_hash": scan_hash, "decision": asdict(decision), "shortlist": selected_symbols})
        if decision.action == "HOLD":
            return {"action": "HOLD", "symbol": "NONE", "reason": decision.reason, "eligible": len(ranked), "shortlist": selected_symbols, "equity_tmn": str(equity)}
        if decision.symbol not in selected_symbols:
            raise ValueError("decision outside shortlist")
        view = by_symbol[decision.symbol]
        if decision.action == "BUY" and reasons[view.symbol] != "eligible":
            self.log.append("risk", view.symbol, {"approved": False, "reason": reasons[view.symbol]})
            return {"action": decision.action, "symbol": view.symbol, "risk": reasons[view.symbol]}
        price = None
        if fill_prices:
            buy, sell = fill_prices[view.symbol]
            price = buy if decision.action == "BUY" else sell
        return self.step(view, mode, simulated_fill_price=price, decision=decision, mark_timestamp=mark_time)


def collect_live_views(api: WallexPublic, package_id: int) -> tuple:
    markets = api.markets()
    unavailable = api.unavailable(package_id)
    now = int(time.time())
    last_complete_hour = now // 3600 * 3600 - 3600
    allowed_tmn = sorted(s for s, market in markets.items() if market.get("quoteAsset") == "TMN" and market.get("spot_active") and s not in unavailable)
    allowed_usdt = sorted(s for s, market in markets.items() if market.get("quoteAsset") == "USDT" and market.get("spot_active") and s not in unavailable)

    def fetch(symbol: str) -> MarketView:
        candles = [x for x in api.candles(symbol, last_complete_hour - 76 * 3600, last_complete_hour + 3600) if x["timestamp"] <= last_complete_hour]
        if not candles or candles[-1]["timestamp"] != last_complete_hour:
            raise RemoteError("stale candle data")
        bid, ask, bid_depth, ask_depth = book_levels(api.depth(symbol))
        view = features(symbol, candles, bid, ask, bid_depth, ask_depth, True, True)
        if view.volatility_3d_12h <= 0:
            raise RemoteError("insufficient continuous candles")
        return view

    views, errors = [], {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(fetch, s): s for s in allowed_tmn}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                views.append(job.result())
            except (RemoteError, ValueError, KeyError, ArithmeticError, OverflowError) as exc:
                errors[symbol] = str(exc) if isinstance(exc, RemoteError) else type(exc).__name__
    views.sort(key=lambda x: x.symbol)
    return views, {"allowed_tmn": allowed_tmn, "allowed_usdt": allowed_usdt, "errors": errors, "last_complete_hour": last_complete_hour}


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
    engine = Engine(GlobalOpenAIDecider() if model == "openai" else GlobalRuleDecider(), portfolio, log)
    try:
        views, catalog = collect_live_views(api, package_id)
        log.append("universe_catalog", None, catalog)
        if not views or any(symbol not in {v.symbol for v in views} for symbol in portfolio.positions):
            raise RemoteError("open position has no fresh market view")
        engine.prices = {v.symbol: v.bid for v in views}
        result = engine.cycle(views, "shadow")
        log.save_state("shadow_portfolio", {"cash": str(portfolio.cash), "positions": {s: asdict(p) for s, p in portfolio.positions.items()}, "peak_equity": str(portfolio.peak_equity), "day_start_equity": str(portfolio.day_start_equity), "day": str(portfolio.day) if portfolio.day else None, "total_stop": portfolio.total_stop, "daily_stop": portfolio.daily_stop})
    except (RemoteError, ValueError, KeyError) as exc:
        log.append("cycle_error", None, {"error_type": type(exc).__name__, "action": "no_new_orders"})
        raise
    return [{**result, "allowed_tmn": len(catalog["allowed_tmn"]), "allowed_usdt_cataloged": len(catalog["allowed_usdt"]), "scanned": len(views), "errors": len(catalog["errors"])}]


def run_backtest(db: str, start: int, end: int, model: str = "rule", spread_bps: Decimal = D(20), package_id: int = 183) -> dict:
    if model not in ("rule", "openai") or start >= end or spread_bps <= 0:
        raise ValueError("invalid backtest inputs")
    api = WallexPublic()
    markets, unavailable = api.markets(), api.unavailable(package_id)
    symbols = sorted(s for s, market in markets.items() if market.get("quoteAsset") == "TMN" and market.get("spot_active") and s not in unavailable)
    histories, fetch_errors = {}, {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(api.candles, s, start - 76 * 3600, end): s for s in symbols}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                histories[symbol] = job.result()
            except (RemoteError, ValueError, KeyError) as exc:
                fetch_errors[symbol] = type(exc).__name__
    log = AuditLog(db)
    log.append("backtest_catalog", None, {"allowed_tmn": symbols, "fetch_errors": fetch_errors})
    settings = RiskSettings()
    portfolio = Portfolio(settings)
    engine = Engine(GlobalOpenAIDecider() if model == "openai" else GlobalRuleDecider(), portfolio, log)
    rows_by_time: Dict[int, list] = {}
    for symbol, rows in histories.items():
        for i in range(73, len(rows)):
            next_bar = rows[i]
            if start <= next_bar["timestamp"] < end and rows[i - 1]["timestamp"] + 3600 == next_bar["timestamp"]:
                rows_by_time.setdefault(next_bar["timestamp"], []).append((symbol, rows[:i], next_bar))
    equity_curve = []
    decisions = 0
    for timestamp, items in sorted(rows_by_time.items()):
        engine.prices.update({symbol: seen[-1]["close"] * (D(1) - spread_bps / D(20000)) for symbol, seen, _ in items})
        views, fill_prices = [], {}
        for symbol, seen, next_bar in items:
            c = seen[-1]["close"]
            bid = c * (D(1) - spread_bps / D(20000))
            ask = c * (D(1) + spread_bps / D(20000))
            # Historical depth is unavailable: this is an optimistic constant-depth assumption.
            depth = settings.starting_equity
            views.append(features(symbol, seen, bid, ask, depth, depth, True, True))
            fill_prices[symbol] = (next_bar["open"] * (D(1) + spread_bps / D(20000)), next_bar["open"] * (D(1) - spread_bps / D(20000)))
        if views:
            missing_held = sorted(set(portfolio.positions) - {v.symbol for v in views})
            if missing_held:
                log.append("backtest_cycle_hold", None, {"timestamp": timestamp, "reason": "held_market_missing_bar", "symbols": missing_held})
                portfolio.mark(timestamp - 3600, engine.prices)
            else:
                engine.cycle(views, "backtest", fill_prices, decision_timestamp=timestamp)
                decisions += 1
        engine.prices.update({symbol: next_bar["open"] * (D(1) - spread_bps / D(20000)) for symbol, _, next_bar in items})
        portfolio.mark(timestamp, engine.prices)
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
    summary = {"kind": "historical_simulation", "decision_model": model, "allowed_tmn_markets": len(symbols), "loaded_markets": len(histories), "fetch_errors": fetch_errors, "volatility_windows": "six completed 12-hour windows; paired into three days weighted 50/30/20", "rank_weights": {"volatility": "0.25", "trend": "0.35", "volume": "0.20", "spread_cost": "0.20"}, "assumed_spread_bps": str(spread_bps), "fee_fraction_per_side": str(settings.taker_fee_fraction), "start": start, "end": end, "decisions": decisions, "start_equity_tmn": str(settings.starting_equity), "final_equity_tmn": str(final), "net_return_pct": str((final / settings.starting_equity - 1) * 100), "max_drawdown_pct": str(max_drawdown * 100), "equity_curve": equity_curve, "limitations": ["Signals use completed bars and fill at next bar open", "Current market list introduces survivorship bias", "No historical order-book depth; constant assumed spread and depth", "Full fills at synthetic next-open", "Rule model is an engineering baseline, not AI"] if model == "rule" else ["Signals use completed bars and fill at next bar open", "Current market list introduces survivorship bias", "No historical order-book depth; constant assumed spread and depth", "Full fills at synthetic next-open"]}
    log.append("backtest_summary", None, summary)
    return summary
