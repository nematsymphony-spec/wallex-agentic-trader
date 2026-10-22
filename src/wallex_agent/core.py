from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


SYMBOLS = ("BTCTMN", "ETHTMN", "SOLTMN", "PUMPTMN")
D = Decimal


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class MarketView:
    symbol: str
    timestamp: int
    close: Decimal
    bid: Decimal
    ask: Decimal
    bid_quote_depth: Decimal
    ask_quote_depth: Decimal
    momentum_3: Decimal
    momentum_12: Decimal
    volatility_12: Decimal
    volume_quote: Decimal
    market_active: bool
    credit_allowed: bool

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * D(10000)

    def model_input(self) -> dict:
        return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Decision:
    action: str
    symbol: str
    reason: str
    confidence: Decimal
    signal_ids: List[str]
    model: str
    input_hash: str

    @classmethod
    def hold(cls, symbol: str, reason: str, input_hash: str = "", model: str = "fallback") -> "Decision":
        return cls("HOLD", symbol, reason, D(0), [], model, input_hash)


@dataclass(frozen=True)
class RiskSettings:
    starting_equity: Decimal = D(5_000_000)
    max_position_fraction: Decimal = D("0.20")
    max_open_positions: int = 2
    daily_drawdown_fraction: Decimal = D("0.02")
    total_drawdown_fraction: Decimal = D("0.05")
    taker_fee_fraction: Decimal = D("0.003")
    max_spread_bps: Decimal = D(35)
    min_depth_multiple: Decimal = D(5)
    min_order_tmn: Decimal = D(100_000)
    target_position_fraction: Decimal = D("0.15")


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    entry_cost_tmn: Decimal


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: Decimal
    limit_price: Decimal
    notional_tmn: Decimal
    client_id: str
    decision_hash: str


class Portfolio:
    def __init__(self, settings: RiskSettings, cash: Optional[Decimal] = None):
        self.settings = settings
        self.cash = settings.starting_equity if cash is None else cash
        self.positions: Dict[str, Position] = {}
        self.peak_equity = self.cash
        self.day_start_equity = self.cash
        self.day = None
        self.total_stop = False
        self.daily_stop = False

    def equity(self, prices: Dict[str, Decimal]) -> Decimal:
        return self.cash + sum((p.quantity * prices.get(s, D(0)) for s, p in self.positions.items()), D(0))

    def mark(self, timestamp: int, prices: Dict[str, Decimal]) -> Decimal:
        day = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Tehran")).date()
        eq = self.equity(prices)
        if self.day != day:
            self.day, self.day_start_equity, self.daily_stop = day, eq, False
        self.peak_equity = max(self.peak_equity, eq)
        if eq <= self.day_start_equity * (D(1) - self.settings.daily_drawdown_fraction):
            self.daily_stop = True
        if eq <= self.settings.starting_equity * (D(1) - self.settings.total_drawdown_fraction):
            self.total_stop = True
        return eq

    def apply_fill(self, intent: OrderIntent, filled_quantity: Decimal, fill_price: Decimal, fee_tmn: Decimal) -> None:
        if filled_quantity <= 0 or filled_quantity > intent.quantity or fee_tmn < 0:
            raise ValueError("invalid fill")
        value = filled_quantity * fill_price
        old = self.positions.get(intent.symbol)
        if intent.side == "BUY":
            if value + fee_tmn > self.cash:
                raise ValueError("insufficient cash")
            self.cash -= value + fee_tmn
            self.positions[intent.symbol] = Position(intent.symbol, filled_quantity + (old.quantity if old else D(0)), value + fee_tmn + (old.entry_cost_tmn if old else D(0)))
        else:
            if old is None or filled_quantity > old.quantity:
                raise ValueError("insufficient position")
            self.cash += value - fee_tmn
            remaining = old.quantity - filled_quantity
            if remaining <= 0:
                del self.positions[intent.symbol]
            else:
                self.positions[intent.symbol] = Position(intent.symbol, remaining, old.entry_cost_tmn * remaining / old.quantity)


def validate_decision(raw: dict, view: MarketView, model: str, input_hash: str) -> Decision:
    if not isinstance(raw, dict) or set(raw) != {"action", "symbol", "reason", "confidence", "signal_ids"}:
        raise ValueError("decision schema")
    if raw["action"] not in ("BUY", "SELL", "HOLD") or raw["symbol"] != view.symbol:
        raise ValueError("invalid action or symbol")
    if not isinstance(raw["reason"], str) or not 8 <= len(raw["reason"]) <= 400:
        raise ValueError("invalid reason")
    if not isinstance(raw["signal_ids"], list) or any(x not in ("momentum_3", "momentum_12", "volatility_12", "spread_bps", "volume_quote", "bid_quote_depth", "ask_quote_depth") for x in raw["signal_ids"]):
        raise ValueError("invalid signals")
    confidence = D(str(raw["confidence"]))
    if not confidence.is_finite() or not D(0) <= confidence <= D(1):
        raise ValueError("invalid confidence")
    return Decision(raw["action"], view.symbol, raw["reason"], confidence, raw["signal_ids"], model, input_hash)


def risk_check(decision: Decision, view: MarketView, portfolio: Portfolio, settings: RiskSettings, prices: Dict[str, Decimal]) -> tuple:
    if decision.action == "HOLD":
        return None, "hold"
    if not view.market_active or not view.credit_allowed or view.symbol not in SYMBOLS:
        return None, "market_not_allowed"
    if view.close <= 0 or view.bid <= 0 or view.ask <= view.bid or not math.isfinite(float(view.spread_bps)):
        return None, "bad_market_data"
    if view.spread_bps > settings.max_spread_bps:
        return None, "spread_too_wide"
    if portfolio.total_stop and decision.action == "BUY":
        return None, "total_drawdown_stop"
    equity = portfolio.equity(prices)
    if decision.action == "BUY":
        if portfolio.daily_stop:
            return None, "daily_drawdown_stop"
        if decision.symbol in portfolio.positions:
            return None, "position_already_open"
        if len(portfolio.positions) >= settings.max_open_positions:
            return None, "max_open_positions"
        desired_notional = min(equity * settings.target_position_fraction, equity * settings.max_position_fraction, portfolio.cash / (D(1) + settings.taker_fee_fraction))
        liquidity_cap = min(view.ask_quote_depth, view.bid_quote_depth) / settings.min_depth_multiple
        notional = min(desired_notional, liquidity_cap)
        if notional < settings.min_order_tmn:
            return None, "insufficient_two_sided_depth" if liquidity_cap < settings.min_order_tmn else "below_min_order"
        price = view.ask
        qty = notional / price
        side = "BUY"
    else:
        pos = portfolio.positions.get(decision.symbol)
        if pos is None:
            return None, "no_position"
        notional = min(pos.quantity * view.bid, view.bid_quote_depth / settings.min_depth_multiple)
        if notional < settings.min_order_tmn:
            return None, "insufficient_bid_depth"
        price, qty, side = view.bid, notional / view.bid, "SELL"
    decision_hash = canonical_hash(asdict(decision))
    order_id = "sc-" + canonical_hash({"decision": decision_hash, "timestamp": view.timestamp, "side": side})[:24]
    return OrderIntent(view.symbol, side, qty, price, notional, order_id, decision_hash), "approved"
