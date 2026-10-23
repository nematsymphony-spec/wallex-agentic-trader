"""All-in hourly BTC/PUMP comparison over the same 57h intraday test window.

Every signal uses completed hourly candles and fills at the next hourly open.
The outcome is an exploratory historical simulation, never an order request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from wallex_agent.clients import WallexPublic

SYMBOLS = ("BTCTMN", "PUMPTMN")
HOUR = 3600
START = 1792465200  # 2026-10-20 03:00 UTC
END = 1792670400  # 2026-10-22 12:00 UTC; 57 completed hours
WARMUP = START - 7 * 86400
INITIAL = 5_000_000.0
TEHRAN = ZoneInfo("Asia/Tehran")
STRATEGIES = ("reversal_1h", "momentum_volume_1h", "breakout_1h", "ema_1h")


def acquire(path: Path) -> dict:
    if path.exists():
        data = json.loads(path.read_text())
        if data["warmup"] != WARMUP or data["start"] != START or data["end"] != END:
            raise ValueError("cached data window mismatch")
        return data
    api = WallexPublic()
    out = {"source": "Wallex /v1/udf/history resolution=60", "warmup": WARMUP,
           "start": START, "end": END, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
           "symbols": {}}
    for symbol in SYMBOLS:
        rows = api.candles(symbol, WARMUP, END, "60")
        out["symbols"][symbol] = [{k: str(v) if k != "timestamp" else v for k, v in row.items()}
                                  for row in rows if WARMUP <= row["timestamp"] < END]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return out


def parse(raw: list[dict]) -> list[dict]:
    rows = [{"ts": int(x["timestamp"]), **{k: float(x[k]) for k in ("open", "high", "low", "close", "volume")}}
            for x in raw]
    expected = list(range(WARMUP, END, HOUR))
    if [x["ts"] for x in rows] != expected:
        raise ValueError("hourly history is incomplete or duplicated")
    if any(x["low"] > min(x["open"], x["close"]) or x["high"] < max(x["open"], x["close"])
           or x["close"] <= 0 or x["volume"] < 0 for x in rows):
        raise ValueError("invalid hourly OHLCV")
    return rows


def ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def enter(name: str, bars: list[dict], i: int, fast: list[float], slow: list[float]) -> bool:
    p = i - 1  # latest completed bar
    if name == "reversal_1h":
        return bars[p]["close"] < bars[p]["open"]
    if name == "momentum_volume_1h":
        med = statistics.median(x["volume"] for x in bars[p - 20:p])
        return (med > 0 and bars[p]["volume"] >= 1.5 * med
                and bars[p]["close"] > max(x["high"] for x in bars[p - 8:p])
                and bars[p]["close"] > bars[p - 4]["close"])
    if name == "breakout_1h":
        med = statistics.median(x["volume"] for x in bars[p - 24:p])
        return (med > 0 and bars[p]["volume"] >= 1.5 * med
                and bars[p]["close"] > max(x["high"] for x in bars[p - 12:p])
                and bars[p]["close"] > bars[p - 12]["close"])
    if name == "ema_1h":
        return fast[p] > slow[p] and fast[p - 1] <= slow[p - 1]
    raise ValueError(name)


def leave(name: str, bars: list[dict], i: int, entry_i: int,
          fast: list[float], slow: list[float]) -> bool:
    p = i - 1
    if name == "reversal_1h":
        return i - entry_i >= 1
    if name == "momentum_volume_1h":
        return i - entry_i >= 12 or bars[p]["close"] < min(x["low"] for x in bars[p - 4:p])
    if name == "breakout_1h":
        return i - entry_i >= 12 or bars[p]["close"] < min(x["low"] for x in bars[p - 6:p])
    if name == "ema_1h":
        return i - entry_i >= 24 or fast[p] < slow[p]
    raise ValueError(name)


def summary(cash: float, peak: float, drawdown: float, fees: float,
            trades: list[dict], stopped: bool) -> dict:
    return {"net_return_pct": (cash / INITIAL - 1) * 100, "net_pnl_tmn": cash - INITIAL,
            "max_drawdown_pct": drawdown * 100, "trades": len(trades),
            "wins": sum(t["pnl_tmn"] > 0 for t in trades), "fees_tmn": fees,
            "total_stop_triggered": stopped, "trade_log": trades}


def backtest(bars: list[dict], name: str, fee: float, spread_bps: float) -> dict:
    half = spread_bps / 20_000
    first = (START - WARMUP) // HOUR
    last = (END - WARMUP) // HOUR
    prices = [b["close"] for b in bars]
    fast, slow = ema(prices, 6), ema(prices, 24)
    cash, qty, basis, entry_i = INITIAL, 0.0, 0.0, -1
    peak, drawdown, fees, trades = INITIAL, 0.0, 0.0, []
    day, day_start, daily_stop, total_stop = None, INITIAL, False, False
    for i in range(first, last):
        b = bars[i]
        marked_open = cash + qty * b["open"] * (1 - half) * (1 - fee)
        local_day = datetime.fromtimestamp(b["ts"], TEHRAN).date()
        if local_day != day:
            day, day_start, daily_stop = local_day, marked_open, False
        daily_stop |= marked_open <= day_start * .98
        total_stop |= marked_open <= INITIAL * .95
        if qty and (total_stop or leave(name, bars, i, entry_i, fast, slow)):
            proceeds = qty * b["open"] * (1 - half)
            exit_fee = proceeds * fee
            fees += exit_fee
            cash += proceeds - exit_fee
            trades.append({"entry_ts": bars[entry_i]["ts"], "exit_ts": b["ts"],
                           "pnl_tmn": proceeds - exit_fee - basis})
            qty, entry_i = 0.0, -1
        if not qty and not daily_stop and not total_stop and enter(name, bars, i, fast, slow):
            # Spend all currently available cash, reserving the buy-side fee.
            size = cash / (1 + fee)
            buy_fee = size * fee
            fill = b["open"] * (1 + half)
            qty, basis, entry_i = size / fill, size + buy_fee, i
            cash -= basis
            fees += buy_fee
        equity = cash + qty * b["close"] * (1 - half) * (1 - fee)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        daily_stop |= equity <= day_start * .98
        total_stop |= equity <= INITIAL * .95
    if qty:
        proceeds = qty * bars[last - 1]["close"] * (1 - half)
        exit_fee = proceeds * fee
        cash += proceeds - exit_fee
        fees += exit_fee
        trades.append({"entry_ts": bars[entry_i]["ts"], "exit_ts": END,
                       "pnl_tmn": proceeds - exit_fee - basis})
    return summary(cash, peak, drawdown, fees, trades, total_stop)


def buy_hold(bars: list[dict], fee: float, spread_bps: float) -> dict:
    half = spread_bps / 20_000
    first = (START - WARMUP) // HOUR
    last = (END - WARMUP) // HOUR
    size = INITIAL / (1 + fee)
    qty = size / (bars[first]["open"] * (1 + half))
    cash = INITIAL - size * (1 + fee)
    peak, drawdown = INITIAL, 0.0
    for b in bars[first:last]:
        equity = cash + qty * b["close"] * (1 - half) * (1 - fee)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
    proceeds = qty * bars[last - 1]["close"] * (1 - half)
    exit_fee = proceeds * fee
    cash += proceeds - exit_fee
    return summary(cash, peak, drawdown, size * fee + exit_fee,
                   [{"entry_ts": START, "exit_ts": END, "pnl_tmn": cash - INITIAL}], False)


def rotate(bars: dict[str, list[dict]], fee: float, spread_bps: float) -> dict:
    half = spread_bps / 20_000
    first = (START - WARMUP) // HOUR
    last = (END - WARMUP) // HOUR
    cash, held, qty, basis, entry_i = INITIAL, None, 0.0, 0.0, -1
    peak, drawdown, fees, trades = INITIAL, 0.0, 0.0, []
    day, day_start, daily_stop, total_stop = None, INITIAL, False, False
    for i in range(first, last):
        b = bars[SYMBOLS[0]][i]
        scores = {s: bars[s][i - 1]["close"] / bars[s][i - 5]["close"] - 1 for s in SYMBOLS}
        winner = max(scores, key=scores.get)
        target = winner if scores[winner] > .008 else None
        marked_open = cash + (qty * bars[held][i]["open"] * (1 - half) * (1 - fee) if held else 0)
        local_day = datetime.fromtimestamp(b["ts"], TEHRAN).date()
        if local_day != day:
            day, day_start, daily_stop = local_day, marked_open, False
        daily_stop |= marked_open <= day_start * .98
        total_stop |= marked_open <= INITIAL * .95
        if held and (total_stop or i - entry_i >= 8 or target != held):
            proceeds = qty * bars[held][i]["open"] * (1 - half)
            exit_fee = proceeds * fee
            cash += proceeds - exit_fee
            fees += exit_fee
            trades.append({"symbol": held, "entry_ts": bars[held][entry_i]["ts"],
                           "exit_ts": b["ts"], "pnl_tmn": proceeds - exit_fee - basis})
            held, qty = None, 0.0
        if not held and not daily_stop and not total_stop and target:
            size = cash / (1 + fee)
            buy_fee = size * fee
            fill = bars[target][i]["open"] * (1 + half)
            qty, basis, entry_i, held = size / fill, size + buy_fee, i, target
            cash -= basis
            fees += buy_fee
        equity = cash + (qty * bars[held][i]["close"] * (1 - half) * (1 - fee) if held else 0)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        daily_stop |= equity <= day_start * .98
        total_stop |= equity <= INITIAL * .95
    if held:
        proceeds = qty * bars[held][last - 1]["close"] * (1 - half)
        exit_fee = proceeds * fee
        cash += proceeds - exit_fee
        fees += exit_fee
        trades.append({"symbol": held, "entry_ts": bars[held][entry_i]["ts"],
                       "exit_ts": END, "pnl_tmn": proceeds - exit_fee - basis})
    return summary(cash, peak, drawdown, fees, trades, total_stop)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("results/hourly-btc-pump-data.json"))
    parser.add_argument("--output", type=Path, default=Path("results/hourly-btc-pump-57h-all-in.json"))
    parser.add_argument("--fee", type=float, default=.003)
    parser.add_argument("--spread-bps", type=float, default=20)
    args = parser.parse_args()
    if not 0 <= args.fee < 1 or args.spread_bps < 0:
        parser.error("invalid costs")
    data = acquire(args.data)
    bars = {s: parse(data["symbols"][s]) for s in SYMBOLS}
    results = {}
    for symbol in SYMBOLS:
        results[symbol] = {"buy_hold_all_in": buy_hold(bars[symbol], args.fee, args.spread_bps)}
        for strategy in STRATEGIES:
            results[symbol][strategy] = backtest(bars[symbol], strategy, args.fee, args.spread_bps)
    results["BTC_PUMP_ROTATION"] = {"relative_strength_1h": rotate(bars, args.fee, args.spread_bps)}
    output = {"kind": "exploratory_historical_simulation", "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "start_utc": datetime.fromtimestamp(START, timezone.utc).isoformat(),
              "end_utc": datetime.fromtimestamp(END, timezone.utc).isoformat(),
              "warmup_hours": (START - WARMUP) // HOUR, "test_hours": (END - START) // HOUR,
              "assumptions": {"starting_cash_tmn": INITIAL, "allocation": "all available cash on each buy",
                              "fee_each_side": args.fee, "full_spread_bps": args.spread_bps,
                              "risk": "2% Tehran-day pause on new buys; 5% loss from initial equity exits at next open and stops new buys",
                              "execution": "next hourly open after completed signal; terminal sale at final completed close",
                              "historical_depth": "unavailable", "slippage": "not modeled", "credit_daily_fee": "not modeled"},
              "results": results, "limitations": ["One 57-hour exploratory window; no out-of-sample or random placebo",
                                                "No historical bid/ask/depth; spread is assumed", "Account fees and credit daily fee unverified",
                                                "No AI inference; rotation is a deterministic proxy"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    for symbol, entries in results.items():
        print(symbol)
        for name, x in entries.items():
            print(f"  {name:23s} {x['net_return_pct']:>8.3f}%  trades={x['trades']:>3}  wins={x['wins']:>3}  dd={x['max_drawdown_pct']:>6.3f}%")


if __name__ == "__main__":
    main()
