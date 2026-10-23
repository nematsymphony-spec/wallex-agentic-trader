"""Completed-bar monthly-goal strategy; historical proxy and live execution gates."""
import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP


def relative_score(symbol, coin, fx, btc, kind="r24"):
    vals = [coin[k] for k in ("r24", "r72", "rv24", "rv72")]
    if not all(math.isfinite(float(v)) for v in vals + [fx["r24"], fx["r72"], btc["r24"], btc["r72"]]):
        return None
    if coin["r24"] <= max(0, fx["r24"]) or coin["r72"] <= fx["r72"]:
        return None
    ref = fx if symbol == "BTCTMN" else btc
    r24 = (coin["r24"] - ref["r24"]) / max(.001, coin["rv24"] * math.sqrt(24))
    r72 = (coin["r72"] - ref["r72"]) / max(.001, coin["rv72"] * math.sqrt(72))
    score = r24 if kind == "r24" else r72 if kind == "r72" else (r24 + r72) / 2
    return float(score) if score > 0 else None


def percentile(values, x):
    return 50. if len(values) == 1 else 100 * (sum(v < x for v in values) + (sum(v == x for v in values) - 1) / 2) / (len(values) - 1)


def collect(api, now):
    hour, day = int(now) // 3600 * 3600, int(now) // 86400 * 86400
    markets, banned = api.markets(), api.unavailable(183)
    allowed = {s: m for s, m in markets.items() if m.get("spot_active") and m.get("quoteAsset") == "TMN" and s not in banned}
    errors, data = {}, {}

    def fetch(s):
        hourly = api.candles(s, day - 8 * 86400, hour - 1)
        daily = api.candles(s, day - 8 * 86400, day - 1, "1D")
        hs = {b["timestamp"]: {k: float(v) for k, v in b.items()} for b in hourly}
        ds = {b["timestamp"]: {k: float(v) for k, v in b.items()} for b in daily}
        for b in list(hs.values()) + list(ds.values()):
            o, h, l, c, v = (b[k] for k in ("open", "high", "low", "close", "volume"))
            if not all(math.isfinite(x) for x in (o, h, l, c, v)) or min(o, l, c) <= 0 or h < max(o, l, c) or l > min(o, c) or v < 0:
                raise ValueError("Invalid OHLCV")
        # Only fill missing hours when the FULL prior day's volume reconciles.
        for t, b in sorted(ds.items()):
            if t >= day:
                continue
            total = sum(hs[x]["volume"] for x in range(t, t + 86400, 3600) if x in hs)
            if math.isclose(total, b["volume"], rel_tol=1e-6, abs_tol=1e-6):
                for x in range(t, t + 86400, 3600):
                    if x not in hs and x - 3600 in hs:
                        c = hs[x - 3600]["close"]
                        hs[x] = {"timestamp": x, "open": c, "high": c, "low": c, "close": c, "volume": 0.}
        closes = [hs[t]["close"] for t in range(hour - 73 * 3600, hour, 3600)]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        f = {"r24": closes[-1] / closes[-25] - 1, "r72": closes[-1] / closes[0] - 1,
             "rv24": statistics.pstdev(rets[-24:]), "rv72": statistics.pstdev(rets)}
        return hs, ds, f

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {s: pool.submit(fetch, s) for s in allowed}
        for s, future in futures.items():
            try:
                data[s] = future.result()
            except Exception as exc:
                errors[s] = type(exc).__name__
    rows = []
    for s, (hs, ds, f) in data.items():
        try:
            from datetime import datetime
            listed = allowed[s].get("createdAt") or allowed[s]["hector"]["created_at"]
            if datetime.fromisoformat(listed.replace("Z", "+00:00")).timestamp() > day - 7 * 86400:
                continue
            past = [ds[t] for t in range(day - 7 * 86400, day, 86400)]
            if any(b["volume"] <= 0 for b in past):
                continue
            prev = [hs[t] for t in range(day - 86400, day, 3600)]
            lo, hi = past[-1]["low"] * past[-1]["volume"], past[-1]["high"] * past[-1]["volume"]
            approx = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in prev)
            if lo < 50_000_000 or sum(b["volume"] > 0 for b in prev) < 18 or not lo * (1 - 1e-6) <= approx <= hi * (1 + 1e-6):
                continue
            ranges = [100 * (b["high"] / b["low"] - 1) for b in past]
            rows.append({"symbol": s, "mean7": statistics.mean(ranges), "mean3": statistics.mean(ranges[-3:]), "turnover_lower": lo, **f})
        except (KeyError, ValueError, TypeError):
            errors[s] = "Incomplete screening data"
    for row in rows:
        row["screen_score"] = sum(w * percentile([r[k] for r in rows], row[k]) for k, w in (("mean7", .4), ("mean3", .3), ("turnover_lower", .1))) / .8
    rows.sort(key=lambda r: (-r["screen_score"], r["symbol"]))
    candidates = []
    if "USDTTMN" in data and "BTCTMN" in data:
        for row in rows[:5]:
            if row["symbol"] in ("USDTTMN", "USDCTMN"):
                continue
            score = relative_score(row["symbol"], row, data["USDTTMN"][2], data["BTCTMN"][2])
            if score is not None:
                candidates.append({**row, "relative_score": score})
    candidates.sort(key=lambda r: (r["relative_score"], r["symbol"]), reverse=True)
    return {"captured_at": now, "hour": hour, "day_boundary": "UTC (03:30 Tehran)",
            "allowed_count": len(allowed), "screen": rows, "candidates": candidates,
            "missing_references": [s for s in ("USDTTMN", "BTCTMN") if s not in data],
            "errors": errors, "historical_cost_component": "unavailable; normalized 40/30/10 proxy"}


def decimal(value):
    value = D(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid monetary value")
    return value


def walk(levels, quantity):
    remaining, total = quantity, D(0)
    for level in levels:
        take = min(remaining, decimal(level["quantity"]))
        total += take * decimal(level["price"])
        remaining -= take
        if remaining == 0:
            break
    if remaining > 0:
        raise ValueError("Insufficient displayed depth")
    return total


def plan_order(meta, book, side, cash, qty, cfg):
    fee = decimal(cfg["fee_rate"])
    bids = sorted(book["bid"], key=lambda x: decimal(x["price"]), reverse=True)
    asks = sorted(book["ask"], key=lambda x: decimal(x["price"]))
    bid, ask = decimal(bids[0]["price"]), decimal(asks[0]["price"])
    if bid <= 0 or ask < bid:
        raise ValueError("Invalid/crossed book")
    tick = D(10) ** -int(meta["tickSize"])
    step = D(10) ** -int(meta["stepSize"])
    slip = decimal(cfg["limit_slippage_bps"]) / 10000
    raw = ask * (1 + slip) if side == "BUY" else bid * (1 - slip)
    price = (raw / tick).to_integral_value(rounding=ROUND_UP if side == "BUY" else ROUND_DOWN) * tick
    levels = asks if side == "BUY" else bids
    executable = [l for l in levels if (decimal(l["price"]) <= price if side == "BUY" else decimal(l["price"]) >= price)]
    quantity = decimal(cash) * decimal(cfg["allocation"]) / (price * (1 + fee)) if side == "BUY" else decimal(qty)
    if side == "SELL":
        # An exit may consume only current bounded depth. The durable exit reason
        # keeps liquidating the remainder after cancellation/reconciliation.
        quantity = min(quantity, sum((decimal(l["quantity"]) for l in executable), D(0)))
    quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if quantity < decimal(meta["minQty"]) or quantity * price < decimal(meta["minNotional"]):
        raise ValueError("Below exchange minimum; dust requires attention")
    proceeds = walk(bids, quantity)
    try:
        buy_cost = walk(asks, quantity)
        roundtrip = 1 - proceeds * (1 - fee) / (buy_cost * (1 + fee))
    except ValueError:
        if side == "BUY":
            raise
        buy_cost, roundtrip = None, None
    if side == "BUY" and roundtrip > decimal(cfg["max_roundtrip_cost"]):
        raise ValueError("Roundtrip execution cost above cap")
    walk(executable, quantity)
    return {"symbol": meta["symbol"], "side": side, "type": "LIMIT", "price": str(price), "quantity": str(quantity)}, {"instant_roundtrip_cost": str(roundtrip), "buy_quote": str(buy_cost), "sell_quote": str(proceeds), "fee_rate_scenario": str(fee)}
