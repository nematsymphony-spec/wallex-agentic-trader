"""Exploratory, reproducible BTC/PUMP intraday strategy comparison.

Fetches Wallex 1-minute candles in short chunks, aggregates only complete
5-minute/15-minute bars, and fills signals at the following bar's open.
All trading results are simulations. Fee/spread are explicit scenarios.
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
MINUTE = 60
FIVE = 300
FIFTEEN = 900
STARTING_CASH = 5_000_000.0
END = 1792670400  # 2026-10-22 12:00 UTC, same end as earlier hourly test.
# Both markets lack 13 source minutes on 2026-10-19 23:08–23:20 UTC.
# Start after that shared gap, with three clean hours of feature warm-up.
CLEAN_START = 1792454400  # 2026-10-20 00:00 UTC
START = 1792465200  # 2026-10-20 03:00 UTC: 57-hour test
WARMUP_START = 1792238400  # download starts 2026-10-17 12:00 UTC


def acquire(path: Path) -> dict:
    if path.exists():
        data = json.loads(path.read_text())
        if data["start"] != WARMUP_START or data["end"] != END:
            raise ValueError("cached data window differs")
        return data
    api = WallexPublic()
    out = {"source": "Wallex /v1/udf/history resolution=1", "start": WARMUP_START, "test_start": START, "end": END,
           "captured_at_utc": datetime.now(timezone.utc).isoformat(), "symbols": {}}
    for symbol in SYMBOLS:
        rows = {}
        for left in range(WARMUP_START, END, 12 * 3600):
            right = min(left + 12 * 3600, END)
            # API endpoints are inclusive. Deduplicate the joining minute.
            for bar in api.candles(symbol, left, right, "1"):
                ts = bar["timestamp"]
                if left <= ts < right:
                    rows[ts] = {k: (str(v) if k != "timestamp" else v) for k, v in bar.items()}
        out["symbols"][symbol] = [rows[t] for t in sorted(rows)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, sort_keys=True, separators=(",", ":")))
    return out


def aggregate(raw: list[dict], seconds: int) -> list[dict]:
    minute_rows = {int(x["timestamp"]): x for x in raw}
    bars = []
    for ts in range(WARMUP_START, END, seconds):
        block = [minute_rows.get(t) for t in range(ts, ts + seconds, MINUTE)]
        if any(x is None for x in block):
            continue
        bars.append({"ts": ts, "open": float(block[0]["open"]), "high": max(float(x["high"]) for x in block),
                     "low": min(float(x["low"]) for x in block), "close": float(block[-1]["close"]),
                     "volume": sum(float(x["volume"]) for x in block)})
    return bars


def ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    answer = [values[0]]
    for value in values[1:]:
        answer.append(alpha * value + (1 - alpha) * answer[-1])
    return answer


def signal(strategy: str, bars: list[dict], i: int, fast: list[float] | None = None,
           slow: list[float] | None = None) -> bool:
    # At open i, all information ends with completed bar i-1.
    p = i - 1
    if strategy == "reversal_15m":
        return p >= 2 and bars[p]["close"] < bars[p]["open"]
    if strategy == "momentum_volume_15m":
        if p < 24:
            return False
        med = statistics.median(x["volume"] for x in bars[p - 20:p])
        return (med > 0 and bars[p]["volume"] >= 1.5 * med
                and bars[p]["close"] > max(x["high"] for x in bars[p - 8:p])
                and bars[p]["close"] > bars[p - 4]["close"])
    if strategy == "breakout_5m":
        if p < 24:
            return False
        med = statistics.median(x["volume"] for x in bars[p - 24:p])
        return (med > 0 and bars[p]["volume"] >= 1.5 * med
                and bars[p]["close"] > max(x["high"] for x in bars[p - 12:p])
                and bars[p]["close"] > bars[p - 12]["close"])
    if strategy == "ema_5m":
        return p >= 24 and fast[p] > slow[p] and fast[p - 1] <= slow[p - 1]
    raise ValueError(strategy)


def exit_signal(strategy: str, bars: list[dict], i: int, entry_i: int,
                fast: list[float] | None = None, slow: list[float] | None = None) -> bool:
    p = i - 1
    if strategy == "reversal_15m":
        return i - entry_i >= 1
    if strategy == "momentum_volume_15m":
        return i - entry_i >= 12 or bars[p]["close"] < min(x["low"] for x in bars[p - 4:p])
    if strategy == "breakout_5m":
        return i - entry_i >= 12 or bars[p]["close"] < min(x["low"] for x in bars[p - 6:p])
    if strategy == "ema_5m":
        return i - entry_i >= 24 or fast[p] < slow[p]
    raise ValueError(strategy)


def backtest(bars: list[dict], strategy: str, fee: float, spread_bps: float,
             allocation: float = .20) -> dict:
    assert all(bars[j]["ts"] - bars[j - 1]["ts"] == bars[1]["ts"] - bars[0]["ts"] for j in range(1, len(bars)))
    half_spread = spread_bps / 20_000
    start_i = next(i for i, b in enumerate(bars) if b["ts"] == START)
    end_i = next(i for i, b in enumerate(bars) if b["ts"] == END - (bars[1]["ts"] - bars[0]["ts"])) + 1
    close = [x["close"] for x in bars]
    fast, slow = ema(close, 6), ema(close, 24)
    cash, quantity, entry_i, basis = STARTING_CASH, 0.0, -1, 0.0
    peak, max_dd, fee_total, trades, curve = STARTING_CASH, 0.0, 0.0, [], []
    day, day_start, daily_stop, total_stop = None, STARTING_CASH, False, False
    for i in range(start_i, end_i):
        bar = bars[i]
        marked_open = cash + quantity * bar["open"] * (1 - half_spread) * (1 - fee)
        this_day = datetime.fromtimestamp(bar["ts"], ZoneInfo("Asia/Tehran")).date()
        if this_day != day:
            day, day_start, daily_stop = this_day, marked_open, False
        daily_stop |= marked_open <= day_start * .98
        total_stop |= marked_open <= STARTING_CASH * .95
        if quantity and (total_stop or exit_signal(strategy, bars, i, entry_i, fast, slow)):
            sell_price = bar["open"] * (1 - half_spread)
            gross = quantity * sell_price
            sell_fee = gross * fee
            cash += gross - sell_fee
            fee_total += sell_fee
            trades.append({"entry_ts": bars[entry_i]["ts"], "exit_ts": bar["ts"],
                           "pnl_tmn": gross - sell_fee - basis})
            quantity, entry_i = 0.0, -1
        if not quantity and not daily_stop and not total_stop and signal(strategy, bars, i, fast, slow):
            buy_price = bar["open"] * (1 + half_spread)
            size = cash / (1 + fee) if allocation == 1 else min(STARTING_CASH * allocation, cash / (1 + fee))
            buy_fee = size * fee
            cash -= size + buy_fee
            fee_total += buy_fee
            quantity, basis, entry_i = size / buy_price, size + buy_fee, i
        equity = cash + quantity * bar["close"] * (1 - half_spread) * (1 - fee)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        daily_stop |= equity <= day_start * .98
        total_stop |= equity <= STARTING_CASH * .95
        curve.append({"ts": bar["ts"], "equity": equity})
    if quantity:
        # No incomplete future candle is used. Terminal sale at last completed close.
        sale = quantity * bars[end_i - 1]["close"] * (1 - half_spread)
        sell_fee = sale * fee
        cash += sale - sell_fee
        fee_total += sell_fee
        trades.append({"entry_ts": bars[entry_i]["ts"], "exit_ts": END, "pnl_tmn": sale - sell_fee - basis})
    return {"net_return_pct": (cash / STARTING_CASH - 1) * 100,
            "net_pnl_tmn": cash - STARTING_CASH, "max_drawdown_pct": max_dd * 100,
            "trades": len(trades), "wins": sum(x["pnl_tmn"] > 0 for x in trades),
            "fees_tmn": fee_total, "daily_stop_at_end": daily_stop,
            "total_stop_triggered": total_stop, "trade_log": trades, "curve": curve}


def buy_hold(bars: list[dict], fee: float, spread_bps: float, allocation: float) -> dict:
    first = next(x for x in bars if x["ts"] == START)
    last = next(x for x in bars if x["ts"] == END - (bars[1]["ts"] - bars[0]["ts"]))
    half_spread = spread_bps / 20_000
    size = min(STARTING_CASH * allocation, STARTING_CASH / (1 + fee))
    quantity = size / (first["open"] * (1 + half_spread))
    cash = STARTING_CASH - size * (1 + fee)
    sale = quantity * last["close"] * (1 - half_spread)
    final = cash + sale * (1 - fee)
    peak, max_dd = STARTING_CASH, 0.0
    for x in bars:
        if START <= x["ts"] < END:
            equity = cash + quantity * x["close"] * (1 - half_spread) * (1 - fee)
            peak = max(peak, equity)
            max_dd = max(max_dd, 1 - equity / peak)
    return {"net_return_pct": (final / STARTING_CASH - 1) * 100,
            "net_pnl_tmn": final - STARTING_CASH, "max_drawdown_pct": max_dd * 100,
            "trades": 1, "wins": int(final > STARTING_CASH), "fees_tmn": size * fee + sale * fee}


def rank_rotation(all_bars: dict[str, list[dict]], fee: float, spread_bps: float,
                  allocation: float = .20) -> dict:
    # Two-coin, deterministic proxy for relative-strength selection; not a trained ML model.
    indexed = {s: {b["ts"]: b for b in bars} for s, bars in all_bars.items()}
    half_spread = spread_bps / 20_000
    cash, held, qty, basis, entered = STARTING_CASH, None, 0.0, 0.0, None
    peak, max_dd, fees, trades = STARTING_CASH, 0.0, 0.0, []
    day, day_start, daily_stop, total_stop = None, STARTING_CASH, False, False
    for ts in range(START, END, FIFTEEN):
        previous = ts - FIFTEEN
        momentum = {}
        for s, idx in indexed.items():
            if all(t in idx for t in [ts, previous, previous - 4 * FIFTEEN]):
                momentum[s] = idx[previous]["close"] / idx[previous - 4 * FIFTEEN]["close"] - 1
        winner = max(momentum, key=momentum.get) if momentum else None
        # Avoid weak rotation; require >0.8% trailing hourly return, above cost hurdle.
        target = winner if winner and momentum[winner] > .008 else None
        marked_open = cash + (qty * indexed[held][ts]["open"] * (1 - half_spread) * (1 - fee) if held else 0)
        this_day = datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran")).date()
        if this_day != day:
            day, day_start, daily_stop = this_day, marked_open, False
        daily_stop |= marked_open <= day_start * .98
        total_stop |= marked_open <= STARTING_CASH * .95
        if held and (total_stop or ts - entered >= 2 * 3600 or target != held):
            gross = qty * indexed[held][ts]["open"] * (1 - half_spread)
            sell_fee = gross * fee
            cash += gross - sell_fee
            fees += sell_fee
            trades.append({"symbol": held, "entry_ts": entered, "exit_ts": ts,
                           "pnl_tmn": gross - sell_fee - basis})
            held, qty = None, 0.0
        if not held and not daily_stop and not total_stop and target and target in momentum:
            size = cash / (1 + fee) if allocation == 1 else min(STARTING_CASH * allocation, cash / (1 + fee))
            fill = indexed[target][ts]["open"] * (1 + half_spread)
            buy_fee = size * fee
            cash -= size + buy_fee
            fees += buy_fee
            held, qty, basis, entered = target, size / fill, size + buy_fee, ts
        equity = cash + (qty * indexed[held][ts]["close"] * (1 - half_spread) * (1 - fee) if held else 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        daily_stop |= equity <= day_start * .98
        total_stop |= equity <= STARTING_CASH * .95
    if held:
        gross = qty * indexed[held][END - FIFTEEN]["close"] * (1 - half_spread)
        sell_fee = gross * fee
        cash += gross - sell_fee
        fees += sell_fee
        trades.append({"symbol": held, "entry_ts": entered, "exit_ts": END,
                       "pnl_tmn": gross - sell_fee - basis})
    return {"net_return_pct": (cash / STARTING_CASH - 1) * 100,
            "net_pnl_tmn": cash - STARTING_CASH, "max_drawdown_pct": max_dd * 100,
            "trades": len(trades), "wins": sum(x["pnl_tmn"] > 0 for x in trades),
            "fees_tmn": fees, "daily_stop_at_end": daily_stop,
            "total_stop_triggered": total_stop, "trade_log": trades}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("results/intraday-btc-pump-1m-data.json"))
    parser.add_argument("--output", type=Path, default=Path("results/intraday-btc-pump-57h.json"))
    parser.add_argument("--fee", type=float, default=.003)
    parser.add_argument("--spread-bps", type=float, default=20)
    parser.add_argument("--allocation", type=float, default=.20,
                        help="Fraction of starting equity used for each active strategy entry")
    args = parser.parse_args()
    if not 0 <= args.fee < 1 or args.spread_bps < 0 or not 0 < args.allocation <= 1:
        parser.error("invalid cost assumption")
    data = acquire(args.data)
    if set(data["symbols"]) != set(SYMBOLS):
        raise ValueError("unexpected symbols")
    bars = {s: {5: [b for b in aggregate(rows, FIVE) if b["ts"] >= CLEAN_START],
                15: [b for b in aggregate(rows, FIFTEEN) if b["ts"] >= CLEAN_START]}
            for s, rows in data["symbols"].items()}
    expected = {5: (END - CLEAN_START) // FIVE, 15: (END - CLEAN_START) // FIFTEEN}
    for s in SYMBOLS:
        for minutes in [5, 15]:
            b = bars[s][minutes]
            if len(b) != expected[minutes] or b[0]["ts"] != CLEAN_START or b[-1]["ts"] != END - minutes * 60:
                raise ValueError(f"incomplete {s} {minutes}m: {len(b)}/{expected[minutes]}")
    results = {}
    for s in SYMBOLS:
        results[s] = {
            "buy_hold_20pct": buy_hold(bars[s][15], args.fee, args.spread_bps, .20),
            "buy_hold_100pct": buy_hold(bars[s][15], args.fee, args.spread_bps, 1.0),
        }
        for name, minutes in [("reversal_15m", 15), ("momentum_volume_15m", 15),
                              ("breakout_5m", 5), ("ema_5m", 5)]:
            results[s][name] = backtest(bars[s][minutes], name, args.fee, args.spread_bps, args.allocation)
    results["BTC_PUMP_ROTATION"] = {"relative_strength_15m": rank_rotation(
        {s: bars[s][15] for s in SYMBOLS}, args.fee, args.spread_bps, args.allocation)}
    output = {"kind": "exploratory_historical_simulation", "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "start_utc": datetime.fromtimestamp(START, timezone.utc).isoformat(),
              "end_utc": datetime.fromtimestamp(END, timezone.utc).isoformat(),
              "source_candles": {s: len(data["symbols"][s]) for s in SYMBOLS},
              "missing_minutes_before_test": "2026-10-19 23:08–23:20 UTC in both markets; clean 57h test selected",
              "assumptions": {"fee_each_side": args.fee, "full_spread_bps": args.spread_bps,
                              "position_allocation": args.allocation, "starting_cash_tmn": STARTING_CASH,
                              "risk": "2% Tehran-day pause on new buys; 5% total loss closes position and stops new buys",
                              "fills": "next completed signal bar open; full spread scenario; final close at last complete bar",
                              "depth": "not available historically; ignored", "slippage": "not modeled",
                              "credit_daily_fee": "not modeled"}, "results": results,
              "limitations": ["Historical bid/ask and depth unavailable", "Account trading and credit fees unverified",
                              "Current allowlist applied retroactively", "One 57-hour evaluation window; no parameter training",
                              "No AI inference; relative-strength rotation is a deterministic proxy",
                              "5m/15m bars are aggregated from 1m Wallex bars"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    for symbol, entries in results.items():
        print(symbol)
        for name, result in entries.items():
            print(f"  {name:23s} {result['net_return_pct']:>8.3f}%  trades={result['trades']:>3}  wins={result['wins']:>3}  dd={result['max_drawdown_pct']:>6.3f}%")


if __name__ == "__main__":
    main()
