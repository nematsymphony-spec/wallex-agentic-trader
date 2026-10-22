"""Reproducible exploratory 72-hour Wallex TMN trend backtest; no exchange orders.

The rule and costs are fixed in this file. Historical depth/spread are unavailable,
so the spread is a scenario, not an observed execution price.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from wallex_agent.clients import WallexPublic
from wallex_agent.core import D, RiskSettings
from wallex_agent.engine import features
from wallex_agent.selector import rank_views

HOUR = 3600
STARTING_CASH = D(5_000_000)
FEE = D("0.003")
SPREAD_BPS = D(20)
HALF_SPREAD = SPREAD_BPS / D(20000)


def acquire(end: int, path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    api = WallexPublic()
    markets, forbidden = api.markets(), api.unavailable(183)
    symbols = sorted(s for s, m in markets.items() if m.get("quoteAsset") == "TMN" and m.get("spot_active") and s not in forbidden)
    histories, errors = {}, {}

    def get(symbol):
        rows = api.candles(symbol, end - 146 * HOUR, end + HOUR)
        return [{k: str(v) if isinstance(v, Decimal) else v for k, v in r.items()} for r in rows if r["timestamp"] <= end]

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(get, symbol): symbol for symbol in symbols}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                histories[symbol] = job.result()
            except Exception as exc:
                errors[symbol] = type(exc).__name__
    data = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "end": end, "package_id": 183, "allowed_tmn": symbols, "fetch_errors": errors, "histories": histories}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")))
    return data


def parse_rows(raw: list) -> dict:
    rows = {}
    for item in raw:
        bar = {k: D(v) if k != "timestamp" else int(v) for k, v in item.items()}
        if not (bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]) or bar["close"] <= 0 or bar["volume"] < 0:
            raise ValueError("invalid candle")
        if bar["timestamp"] in rows:
            raise ValueError("duplicate candle")
        rows[bar["timestamp"]] = bar
    return rows


def history_at(rows: dict, ts: int, count: int) -> list | None:
    series = [rows.get(ts - i * HOUR) for i in range(count - 1, -1, -1)]
    return None if any(x is None for x in series) else series


def atr14(bars: list) -> Decimal:
    ranges = []
    for i in range(len(bars) - 14, len(bars)):
        bar, previous = bars[i], bars[i - 1]
        ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous["close"]), abs(bar["low"] - previous["close"])))
    return sum(ranges, D(0)) / D(14)


def run(data: dict, source_hash: str) -> dict:
    end = int(data["end"])
    start = end - 72 * HOUR
    settings = RiskSettings()
    parsed, bad = {}, {}
    for symbol, raw in data["histories"].items():
        try:
            parsed[symbol] = parse_rows(raw)
        except ValueError as exc:
            bad[symbol] = str(exc)
    cash, positions, trades, curve = STARTING_CASH, {}, [], []
    peak, max_dd, total_fees = STARTING_CASH, D(0), D(0)
    day, day_start, daily_stop, total_stop = None, STARTING_CASH, False, False
    candidate_hours, available_hours, admission_counts = 0, 0, []

    def price(symbol: str, ts: int, field: str) -> Decimal:
        return parsed[symbol][ts][field]

    def equity(ts: int, field: str) -> Decimal:
        return cash + sum((p["qty"] * price(s, ts, field) * (D(1) - HALF_SPREAD) for s, p in positions.items()), D(0))

    def sell(symbol: str, gross_price: Decimal, ts: int, reason: str):
        nonlocal cash, total_fees
        p = positions.pop(symbol)
        fill = gross_price * (D(1) - HALF_SPREAD)
        proceeds = p["qty"] * fill
        fee = proceeds * FEE
        total_fees += fee
        cash += proceeds - fee
        trades.append({"symbol": symbol, "entry_ts": p["entry_ts"], "exit_ts": ts, "entry_price": p["fill"], "exit_price": fill, "quantity": p["qty"], "entry_fee": p["fee"], "exit_fee": fee, "pnl_tmn": proceeds - fee - p["cost"], "reason": reason})

    for ts in range(start, end, HOUR):
        previous = ts - HOUR
        signal_rows, views = {}, []
        for symbol, rows in parsed.items():
            past = history_at(rows, previous, 73)
            if past is None or ts not in rows:
                continue
            c = past[-1]["close"]
            view = features(symbol, past, c * (D(1) - HALF_SPREAD), c * (D(1) + HALF_SPREAD), STARTING_CASH, STARTING_CASH, True, True)
            views.append(view)
            signal_rows[symbol] = past
        if views:
            available_hours += 1
        ranked, reasons = rank_views(views, settings, previous)
        admission_counts.append(len(ranked))
        # Existing positions exit at next open after a completed-candle reversal
        # or six hours since the actual simulated fill.
        for symbol, p in list(positions.items()):
            past = signal_rows.get(symbol)
            if past is None:
                if ts in parsed[symbol]:
                    sell(symbol, price(symbol, ts, "open"), ts, "data_gap")
                continue
            momentum3 = past[-1]["close"] / past[-4]["close"] - 1
            reason = "time_6h" if ts - p["entry_ts"] >= 6 * HOUR else "trend_reversal" if momentum3 <= 0 or past[-1]["close"] < min(x["low"] for x in past[-7:-1]) else None
            if reason:
                sell(symbol, price(symbol, ts, "open"), ts, reason)

        # Mark before deciding whether a new position is allowed.
        marked = equity(ts, "open")
        tehran_day = datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran")).date()
        if tehran_day != day:
            day, day_start, daily_stop = tehran_day, marked, False
        if marked <= day_start * D("0.98"):
            daily_stop = True
        if marked <= STARTING_CASH * D("0.95"):
            total_stop = True

        choices = []
        for item in ranked:
            symbol = item["symbol"]
            if symbol in positions:
                continue
            past = signal_rows[symbol]
            current = past[-1]
            median_volume = D(str(statistics.median(x["volume"] for x in past[-25:-1])))
            if median_volume <= 0:
                continue
            breakout = current["close"] > max(x["high"] for x in past[-13:-1])
            momentum3 = current["close"] / past[-4]["close"] - 1
            momentum12 = current["close"] / past[-13]["close"] - 1
            if breakout and momentum3 > 0 and momentum12 > 0 and current["volume"] >= D("1.5") * median_volume and atr14(past) > 0:
                choices.append(item)
        if choices:
            candidate_hours += 1
        if choices and len(positions) < 2 and not daily_stop and not total_stop:
            best = choices[0]  # ranked list already incorporates bounded volatility, trend, volume and spread.
            symbol = best["symbol"]
            gross = price(symbol, ts, "open")
            fill = gross * (D(1) + HALF_SPREAD)
            notional = min(marked * D("0.15"), marked * D("0.20"), cash / (D(1) + FEE))
            if notional >= settings.min_order_tmn:
                qty = notional / fill
                fee = notional * FEE
                cash -= notional + fee
                total_fees += fee
                positions[symbol] = {"qty": qty, "fill": fill, "fee": fee, "cost": notional + fee, "entry_ts": ts, "stop": fill - D("1.5") * atr14(signal_rows[symbol])}

        # Only a stop is simulated intrabar. If the bar opens below it, the gap
        # executes at the worse open rather than at the stop level.
        for symbol, p in list(positions.items()):
            bar = parsed[symbol].get(ts)
            if bar is not None and bar["low"] <= p["stop"]:
                sell(symbol, min(bar["open"], p["stop"]), ts, "atr_stop")

        marked = equity(ts, "close")
        peak = max(peak, marked)
        max_dd = max(max_dd, (peak - marked) / peak)
        curve.append({"timestamp": ts, "equity_tmn": marked})
        if marked <= day_start * D("0.98"):
            daily_stop = True
        if marked <= STARTING_CASH * D("0.95"):
            total_stop = True

    # Realize the last portfolio at the first open after the 72 completed bars.
    for symbol in list(positions):
        if end not in parsed[symbol]:
            raise ValueError("missing terminal open for held position")
        sell(symbol, price(symbol, end, "open"), end, "test_window_end")
    final = cash
    peak = max(peak, final)
    max_dd = max(max_dd, (peak - final) / peak)
    curve.append({"timestamp": end, "equity_tmn": final})
    return {"kind": "exploratory_historical_simulation", "model": "deterministic_trend_breakout_no_ai", "source_sha256": source_hash, "captured_at_utc": data["captured_at_utc"], "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(), "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(), "allowed_tmn": len(data["allowed_tmn"]), "downloaded": len(data["histories"]), "fetch_errors": data["fetch_errors"], "invalid_histories": bad, "hours_with_73h_continuous_data": available_hours, "candidate_hours": candidate_hours, "average_eligible_markets": str(D(sum(admission_counts)) / D(len(admission_counts))), "assumptions": {"fee_each_side": str(FEE), "assumed_full_spread_bps": str(SPREAD_BPS), "historical_depth": "unavailable; synthetic sufficient depth", "entry": "next hourly open after completed 12h breakout, positive 3h/12h momentum, 1h volume >=1.5x prior-24h median", "selection": "highest existing rank score; all eligible TMN markets considered", "exit": "frozen 1.5x ATR14 stop; next-open reversal or six-hour maximum; terminal liquidation", "position": "15% equity target, max two positions, 2% daily and 5% total new-buy stops"}, "start_equity_tmn": STARTING_CASH, "final_equity_tmn": final, "net_pnl_tmn": final - STARTING_CASH, "net_return_pct": (final / STARTING_CASH - 1) * 100, "max_drawdown_pct": max_dd * 100, "trades": len(trades), "wins": sum(t["pnl_tmn"] > 0 for t in trades), "total_fees_tmn": total_fees, "trade_log": trades, "equity_curve": curve, "limitations": ["Current market allowlist applied to history: survivorship bias", "Historical spread and order-book depth unavailable; synthetic execution", "No actual account fee or credit daily fee", "One 72-hour sample is insufficient to establish an edge", "No AI model call"]}


def main():
    global FEE, SPREAD_BPS, HALF_SPREAD
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", type=int, required=True, help="UTC start of first incomplete hourly candle")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-each-side", type=Decimal, default=FEE)
    parser.add_argument("--spread-bps", type=Decimal, default=SPREAD_BPS)
    args = parser.parse_args()
    if args.fee_each_side < 0 or args.spread_bps <= 0:
        parser.error("fee must be nonnegative and spread must be positive")
    FEE, SPREAD_BPS = args.fee_each_side, args.spread_bps
    HALF_SPREAD = SPREAD_BPS / D(20000)
    data = acquire(args.end, args.data)
    if int(data["end"]) != args.end:
        raise ValueError("cached data end differs from requested end")
    digest = hashlib.sha256(args.data.read_bytes()).hexdigest()
    result = run(data, digest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k in ("start_utc", "end_utc", "allowed_tmn", "downloaded", "candidate_hours", "trades", "wins", "final_equity_tmn", "net_pnl_tmn", "net_return_pct", "max_drawdown_pct", "total_fees_tmn")}, indent=2, default=str))


if __name__ == "__main__":
    main()
