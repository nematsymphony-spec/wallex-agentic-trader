"""Dynamic TMN universe, deterministic shortlist, and one global AI decision."""
from __future__ import annotations

import json
import math
import os
from decimal import Decimal
from typing import Dict, List, Sequence, Tuple

from .clients import RemoteError, request_json
from .core import D, Decision, MarketView, RiskSettings, canonical_hash


SIGNALS = ("volatility_3d_12h", "momentum_3", "momentum_12", "volatility_12", "spread_bps", "volume_quote", "bid_quote_depth", "ask_quote_depth")


def gate(view: MarketView, settings: RiskSettings, last_complete_hour: int) -> str:
    if not view.symbol.endswith("TMN") or not view.market_active or not view.credit_allowed:
        return "not_tmn_spot_credit_market"
    if view.timestamp != last_complete_hour:
        return "stale_candles"
    if view.volatility_3d_12h <= 0:
        return "no_realized_volatility"
    if view.close <= 0 or view.bid <= 0 or view.ask <= view.bid:
        return "bad_book"
    if view.spread_bps > settings.max_spread_bps:
        return "wide_spread"
    if min(view.bid_quote_depth, view.ask_quote_depth) < settings.min_order_tmn * settings.min_depth_multiple:
        return "thin_two_sided_book"
    if view.volume_quote < settings.min_quote_volume_24h:
        return "low_24h_volume"
    return "eligible"


def rank_views(views: Sequence[MarketView], settings: RiskSettings, last_complete_hour: int) -> Tuple[List[dict], Dict[str, str]]:
    reasons = {v.symbol: gate(v, settings, last_complete_hour) for v in views}
    eligible = [v for v in views if reasons[v.symbol] == "eligible"]
    if not eligible:
        return [], reasons

    def percentile(values: List[Decimal], value: Decimal) -> Decimal:
        ordered = sorted(values)
        return D(sum(x <= value for x in ordered)) / D(len(ordered))

    vol = [v.volatility_3d_12h for v in eligible]
    trend = [v.momentum_3 * D("0.4") + v.momentum_12 * D("0.6") for v in eligible]
    volume = [D(str(math.log1p(float(v.volume_quote)))) for v in eligible]
    ranked = []
    for v in eligible:
        trend_value = v.momentum_3 * D("0.4") + v.momentum_12 * D("0.6")
        volume_value = D(str(math.log1p(float(v.volume_quote))))
        vol_rank = percentile(vol, v.volatility_3d_12h)
        trend_rank = percentile(trend, trend_value)
        volume_rank = percentile(volume, volume_value)
        cost_score = max(D(0), D(1) - v.spread_bps / settings.max_spread_bps)
        score = D("0.25") * vol_rank + D("0.35") * trend_rank + D("0.20") * volume_rank + D("0.20") * cost_score
        ranked.append({"symbol": v.symbol, "score": score, "volatility_rank": vol_rank, "trend_rank": trend_rank, "volume_rank": volume_rank, "cost_score": cost_score, "volatility_3d_12h": v.volatility_3d_12h})
    ranked.sort(key=lambda x: (-x["score"], x["symbol"]))
    return ranked, reasons


class GlobalOpenAIDecider:
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

    def decide_all(self, shortlist: Sequence[MarketView], ranked: Sequence[dict], held_symbols: set) -> Decision:
        choices = [v.symbol for v in shortlist]
        score_by_symbol = {item["symbol"]: item["score"] for item in ranked}
        model_input = {"timestamp": shortlist[0].timestamp if shortlist else None, "candidates": [{"symbol": v.symbol, "has_position": v.symbol in held_symbols, "rank_score": str(score_by_symbol.get(v.symbol, D(0))), "momentum_3": str(v.momentum_3), "momentum_12": str(v.momentum_12), "volatility_3d_12h": str(v.volatility_3d_12h), "volatility_12": str(v.volatility_12), "spread_bps": str(v.spread_bps), "volume_quote": str(v.volume_quote), "bid_quote_depth": str(v.bid_quote_depth), "ask_quote_depth": str(v.ask_quote_depth)} for v in shortlist], "max_actions": 1}
        digest = canonical_hash(model_input)
        if not choices:
            return Decision.hold("NONE", "No eligible markets; fail closed", digest, self.model)
        if not self.api_key:
            return Decision.hold("NONE", "OPENAI_API_KEY missing; fail closed", digest, self.model)
        schema = {"type": "object", "properties": {"action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]}, "symbol": {"type": "string", "enum": ["NONE", *choices]}, "reason": {"type": "string"}, "confidence": {"type": "number"}, "signal_ids": {"type": "array", "items": {"type": "string", "enum": list(SIGNALS)}}}, "required": ["action", "symbol", "reason", "confidence", "signal_ids"], "additionalProperties": False}
        payload = {"model": self.model, "store": False, "temperature": 0, "instructions": "Choose at most one spot trade from the supplied candidate list or HOLD with symbol NONE. Volatility is only a bounded ranking hint, not proof of positive return. Require directional evidence strong enough to exceed 0.3% taker fee on each side, spread and slippage. BUY only for a symbol without a position; SELL only to reduce a held position. Explain using named input signals only. Prefer HOLD if uncertain. Do not invent external facts.", "input": json.dumps(model_input, ensure_ascii=False, default=str), "text": {"format": {"type": "json_schema", "name": "global_trade_decision", "strict": True, "schema": schema}}, "max_output_tokens": 350}
        try:
            response = request_json("https://api.openai.com/v1/responses", method="POST", payload=payload, headers={"Authorization": "Bearer " + self.api_key}, timeout=30)
            if response.get("status") != "completed":
                raise ValueError("incomplete")
            texts = [part.get("text") for item in response.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text"]
            if len(texts) != 1:
                raise ValueError("missing output")
            raw = json.loads(texts[0])
            if not isinstance(raw, dict) or set(raw) != {"action", "symbol", "reason", "confidence", "signal_ids"}:
                raise ValueError("schema")
            if raw["action"] not in ("BUY", "SELL", "HOLD") or raw["symbol"] not in ["NONE", *choices]:
                raise ValueError("action/symbol")
            if (raw["action"] == "HOLD") != (raw["symbol"] == "NONE"):
                raise ValueError("hold pairing")
            if raw["action"] == "BUY" and raw["symbol"] in held_symbols or raw["action"] == "SELL" and raw["symbol"] not in held_symbols:
                raise ValueError("position mismatch")
            if not isinstance(raw["reason"], str) or not 8 <= len(raw["reason"]) <= 400:
                raise ValueError("reason")
            if not isinstance(raw["signal_ids"], list) or any(s not in SIGNALS for s in raw["signal_ids"]):
                raise ValueError("signals")
            confidence = D(str(raw["confidence"]))
            if not confidence.is_finite() or not D(0) <= confidence <= D(1):
                raise ValueError("confidence")
            return Decision(raw["action"], raw["symbol"], raw["reason"], confidence, raw["signal_ids"], self.model, digest)
        except (RemoteError, ValueError, KeyError, ArithmeticError, TypeError):
            return Decision.hold("NONE", "AI unavailable or invalid; fail closed", digest, self.model)


class GlobalRuleDecider:
    """Comparable deterministic engineering baseline; not an AI strategy."""

    def decide_all(self, shortlist: Sequence[MarketView], ranked: Sequence[dict], held_symbols: set) -> Decision:
        digest = canonical_hash({"shortlist": [v.model_input() for v in shortlist], "ranked": ranked, "held": sorted(held_symbols)})
        for v in shortlist:
            if v.symbol in held_symbols and (v.momentum_3 < D("-0.004") or v.momentum_12 < D("-0.01")):
                return Decision("SELL", v.symbol, "Rule baseline sell on weakening completed-candle momentum", D("0.5"), ["momentum_3", "momentum_12"], "global-rule-baseline", digest)
        by_symbol = {v.symbol: v for v in shortlist}
        for item in ranked:
            v = by_symbol.get(item["symbol"])
            if v and v.symbol not in held_symbols and v.momentum_3 > D("0.008") and v.momentum_12 > D("0.012") and v.volatility_12 < D("0.045"):
                return Decision("BUY", v.symbol, "Rule baseline buy from eligible ranked markets", D("0.5"), ["volatility_3d_12h", "momentum_3", "momentum_12"], "global-rule-baseline", digest)
        return Decision.hold("NONE", "No rule-baseline opportunity passed", digest, "global-rule-baseline")
