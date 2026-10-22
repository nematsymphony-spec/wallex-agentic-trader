from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any, Dict, List

from .core import Decision, MarketView, canonical_hash, validate_decision


class RemoteError(RuntimeError):
    pass


def request_json(url: str, *, method: str = "GET", payload: dict = None, headers: dict = None, timeout: int = 12) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json", "Accept-Encoding": "identity", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        # Never include request headers or secret-bearing responses in logs.
        raise RemoteError(f"{urllib.parse.urlsplit(url).path}: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise RemoteError("unexpected JSON shape")
    return data


class WallexPublic:
    BASE = "https://api.wallex.ir"

    def get(self, path: str, params: dict = None) -> dict:
        query = "?" + urllib.parse.urlencode(params) if params else ""
        data = request_json(self.BASE + path + query)
        if data.get("success") is False:
            raise RemoteError(f"Wallex rejected {path}")
        return data

    def markets(self) -> Dict[str, dict]:
        a = self.get("/hector/web/v1/markets")["result"]["markets"]
        b = self.get("/v1/markets")["result"]["symbols"]
        active = {item["symbol"]: item for item in a if isinstance(item, dict) and "symbol" in item}
        return {s: {**item, "spot_active": bool(active.get(s, {}).get("is_spot")), "hector": active.get(s, {})} for s, item in b.items()}

    def unavailable(self, package_id: int) -> set:
        result = self.get(f"/v1/prop/packages/{int(package_id)}")["result"]
        if not isinstance(result.get("unavailableMarkets"), list) or int(result.get("detail", {}).get("id", -1)) != package_id:
            raise RemoteError("invalid package market response")
        return set(result["unavailableMarkets"])

    def depth(self, symbol: str) -> dict:
        result = self.get("/v1/depth", {"symbol": symbol})["result"]
        if not result.get("ask") or not result.get("bid"):
            raise RemoteError("empty order book")
        return result

    def candles(self, symbol: str, start: int, end: int, resolution: str = "60") -> List[dict]:
        result = self.get("/v1/udf/history", {"symbol": symbol, "resolution": resolution, "from": start, "to": end})
        if result.get("s") != "ok":
            raise RemoteError("candle response unavailable")
        fields = ("t", "o", "h", "l", "c", "v")
        if any(not isinstance(result.get(k), list) for k in fields) or len({len(result[k]) for k in fields}) != 1:
            raise RemoteError("invalid candle arrays")
        rows = []
        for values in zip(*(result[k] for k in fields)):
            t, o, h, low, c, v = values
            rows.append({"timestamp": int(t), "open": Decimal(str(o)), "high": Decimal(str(h)), "low": Decimal(str(low)), "close": Decimal(str(c)), "volume": Decimal(str(v))})
        return rows


class OpenAIDecider:
    MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    SCHEMA = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
            "symbol": {"type": "string", "enum": list(("BTCTMN", "ETHTMN", "SOLTMN"))},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
            "signal_ids": {"type": "array", "items": {"type": "string", "enum": ["momentum_3", "momentum_12", "volatility_12", "spread_bps", "volume_quote", "bid_quote_depth", "ask_quote_depth"]}},
        },
        "required": ["action", "symbol", "reason", "confidence", "signal_ids"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or self.MODEL

    def decide(self, view: MarketView, has_position: bool) -> Decision:
        model_input = {"market": view.model_input(), "has_position": has_position}
        input_hash = canonical_hash(model_input)
        if not self.api_key:
            return Decision.hold(view.symbol, "OPENAI_API_KEY missing; fail closed", input_hash)
        payload = {
            "model": self.model,
            "store": False,
            "temperature": 0,
            "instructions": "You are a cautious spot trading analyst. Choose BUY, SELL, or HOLD for exactly the given symbol. A trade must have a credible edge beyond 0.3% taker fee each way plus spread. Prefer HOLD when uncertain. BUY only when no position; SELL only with a position. Explain using provided signal IDs. No external facts or forecasts. Reply only in the required schema.",
            "input": json.dumps(model_input, ensure_ascii=False, default=str),
            "text": {"format": {"type": "json_schema", "name": "trade_decision", "strict": True, "schema": self.SCHEMA}},
            "max_output_tokens": 300,
        }
        try:
            data = request_json("https://api.openai.com/v1/responses", method="POST", payload=payload, headers={"Authorization": "Bearer " + self.api_key}, timeout=25)
            if data.get("status") != "completed":
                raise ValueError("incomplete response")
            texts = [part.get("text") for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text"]
            if len(texts) != 1:
                raise ValueError("no single structured output")
            return validate_decision(json.loads(texts[0]), view, self.model, input_hash)
        except (RemoteError, ValueError, KeyError, ArithmeticError):
            return Decision.hold(view.symbol, "AI unavailable or invalid; fail closed", input_hash, self.model)


class RuleDecider:
    """Engineering baseline for tests/backtests; never described as AI results."""

    def decide(self, view: MarketView, has_position: bool) -> Decision:
        data = {"market": view.model_input(), "has_position": has_position}
        if has_position and (view.momentum_3 < Decimal("-0.004") or view.momentum_12 < Decimal("-0.01")):
            action = "SELL"
        elif not has_position and view.momentum_3 > Decimal("0.008") and view.momentum_12 > Decimal("0.012") and view.volatility_12 < Decimal("0.045"):
            action = "BUY"
        else:
            action = "HOLD"
        return Decision(action, view.symbol, "Deterministic baseline from completed candles", Decimal("0.5"), ["momentum_3", "momentum_12", "volatility_12"], "rule-baseline", canonical_hash(data))
