"""Offline, like-for-like benchmark report; never selects a new strategy.

Recalculates all six saved BTC/PUMP buy-and-hold results from frozen prices,
including fee reserve, precision, residual cash and hourly liquidation marks.
"""
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


OUT = Path("results/research-monthly-goals")
INITIAL, FEE, HALF_SPREAD = 5_000_000., .003, .001


def main():
    strategy_path = OUT / "results.json"
    benchmark_path = Path("results/research90-frozen/results.json")
    meta_path = Path("data/research90-frozen/universe.json")
    strategy = json.loads(strategy_path.read_text())
    original = json.loads(benchmark_path.read_text())
    markets = {m["symbol"]: m for m in json.loads(meta_path.read_text())["markets"]}
    end = int(datetime.fromisoformat(original["end_utc"].replace("Z", "+00:00")).timestamp())
    benchmarks = original["buy_hold_hourly_benchmarks"]
    sources = [strategy_path, benchmark_path, meta_path]
    checks, rows = [], []
    for days in (30, 60, 90):
        start = end - days * 86400
        for symbol in ("BTCTMN", "PUMPTMN"):
            result = benchmarks[f"{days}d-{symbol}"]
            path = Path(f"data/research90-frozen/{symbol}-hourly.json")
            if path not in sources:
                sources.append(path)
            hourly = {int(t): b for t, b in json.loads(path.read_text()).items()}
            assert hourly[start]["v"] > 0, "First entry hour must contain trades"
            price = hourly[start]["o"] * (1 + HALF_SPREAD)
            scale = 10 ** markets[symbol]["amount_precision"]
            qty = math.floor(INITIAL / (1 + FEE) / price * scale) / scale
            buy_fee = qty * price * FEE
            cash = INITIAL - qty * price - buy_fee
            peak, drawdown, last, carried = INITIAL, 0., hourly[start]["o"], 0
            for t in range(start, end, 3600):
                bar = hourly.get(t)
                if bar is not None:
                    last = bar["c"]
                else:
                    carried += 1
                marked = cash + qty * last * (1 - HALF_SPREAD) * (1 - FEE)
                peak = max(peak, marked)
                drawdown = max(drawdown, 1 - marked / peak)
            exit_price = last * (1 - HALF_SPREAD)
            sell_fee = qty * exit_price * FEE
            final = cash + qty * exit_price - sell_fee
            trade, = result["trade_log"]
            assert result["trades"] == 1
            assert trade["entry_ts"] == start and trade["exit_ts"] == end
            assert trade["reason"] == "end_of_test_mark_liquidation", "Benchmark must not exit on stop/goal"
            for metric, recalculated in (("return_pct", (final / INITIAL - 1) * 100),
                                         ("final_tmn", final), ("fees_tmn", buy_fee + sell_fee),
                                         ("max_drawdown_pct", drawdown * 100)):
                assert math.isclose(result[metric], recalculated, rel_tol=1e-10, abs_tol=1e-6), (days, symbol, metric, result[metric], recalculated)
            checks.append({"days": days, "symbol": symbol, "recomputed_from_frozen_hourly_prices": True,
                           "exit_only_at_period_end": True, "carried_missing_hour_marks": carried})
        for goal in (3, 5):
            run = strategy["runs"][f"{days}d-goal{goal}-off-OLHC"]
            cfg = strategy["configs"][f"goal{goal}-off"]
            assert cfg["portfolio_loss_reset"] == "off" and cfg["goal_reset_hours"] == 720
            btc, pump = (benchmarks[f"{days}d-{s}"] for s in ("BTCTMN", "PUMPTMN"))
            rows.append({"days": days, "monthly_goal_pct": goal,
                "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(), "end_utc": original["end_utc"],
                "strategy_return_pct": run["return_pct"], "btc_buy_hold_return_pct": btc["return_pct"],
                "pump_buy_hold_return_pct": pump["return_pct"],
                "strategy_minus_btc_pp": run["return_pct"] - btc["return_pct"],
                "strategy_minus_pump_pp": run["return_pct"] - pump["return_pct"],
                "strategy_hourly_drawdown_pct": run["max_drawdown_pct"],
                "btc_hourly_drawdown_pct": btc["max_drawdown_pct"], "pump_hourly_drawdown_pct": pump["max_drawdown_pct"],
                "strategy_final_tmn": run["final_tmn"], "btc_final_tmn": btc["final_tmn"], "pump_final_tmn": pump["final_tmn"],
                "strategy_fees_tmn": run["fees_tmn"], "btc_fees_tmn": btc["fees_tmn"], "pump_fees_tmn": pump["fees_tmn"],
                "strategy_closed_trades": run["trades"], "benchmark_closed_trades": 1})
    report = {"initial_tmn_each_independent_run": INITIAL, "fee_each_side": FEE,
        "assumed_full_spread_bps": 20, "baseline": "100% capital; buy at opening of first tradable hour, sell at final completed hourly close; no rebalancing, goal or triggered stop",
        "strategy": "monthly-goal relative strength, portfolio stop off, 5% position stop and 2% daily buy pause retained",
        "drawdown_frequency": "hourly closes, net of assumed liquidation costs; not intrahour worst loss",
        "risk_matched": False, "parameter_search": False, "verification": checks,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}, "comparisons": rows}
    (OUT / "buy-hold-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with (OUT / "buy-hold-comparison.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print("Verified all six BTC/PUMP benchmarks from frozen hourly prices; wrote six like-for-like comparisons.")


if __name__ == "__main__":
    main()
