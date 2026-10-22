# Exploratory 72-hour trend-breakout backtest

Captured on 2026-10-22 after the 12:00 UTC hourly candle began. The test window is 2026-10-19 12:00 UTC to 2026-10-22 12:00 UTC (15:30 Tehran on both dates). Only completed hourly candles produce signals; orders fill at the next hourly open. Public Wallex candles were saved in [`results/trend-72h-2026-10-22-1200-data.json`](../results/trend-72h-2026-10-22-1200-data.json). The current active/package-183 TMN allowlist was applied to history.

## Predeclared rule

- All 62 currently allowed TMN markets are considered each hour. Markets without 73 consecutive complete hourly bars, 24-hour quote volume of at least 50 million TMN, or the synthetic 20 bps spread gate are excluded. Historical L2 depth is unavailable and is optimistically assumed sufficient.
- Entry: the latest completed close exceeds the **previous** 12-hour high; three-hour and twelve-hour returns are positive; latest hourly base volume is at least 1.5 times the median of the previous 24 complete hours. Among candidates, choose the highest existing universe rank (bounded three-day realized volatility, trend, volume and spread), at most one new trade per hour.
- Position: target 15% of equity, maximum two open positions. Pause new buys after 2% Tehran-day loss or 5% loss against starting equity.
- Exit: frozen 1.5 × ATR(14) stop from entry, next-open exit when three-hour momentum turns nonpositive or a six-hour low breaks, or at six hours. All remaining positions are sold at the first open after the test window.
- Cost scenario: 0.3% fee per side and 20 bps full bid/ask spread. These are **assumptions**, not verified account fees or historical spreads. No slippage, exchange quantity/price precision, or credit daily fee is included.

## Result

| Metric | Result |
|---|---:|
| Starting equity | 5,000,000 TMN |
| Final equity | 4,871,830.51 TMN |
| Net P&L | −128,169.49 TMN |
| Net return | **−2.5634%** |
| Maximum marked drawdown | **2.8672%** |
| Closed round trips | 26 |
| Winning trades | 9 of 26 |
| Simulated trading fees | 115,808.71 TMN |
| Hours with at least one breakout candidate | 34 of 72 |

Exit counts: 10 trend reversals, eight ATR stops, six six-hour exits, and two mandatory window-end liquidations. The [complete result](../results/trend-72h-2026-10-22-1200.json) contains the trade log and hourly equity curve.

A cost sensitivity replay on the **same saved candles** gave −1.7994% at 0.2% fee per side plus 20 bps spread, −0.2547% with zero fee plus 20 bps spread, and +0.4299% at effectively zero execution cost. See [sensitivity data](../results/trend-72h-2026-10-22-1200-sensitivity.json). The zero-cost case is a diagnostic, not an executable scenario. The simulated spread and fee turn a modest gross gain into a loss. BTCTMN itself rose 3.3751% from opening price to opening price in this window; that is market context, not a risk-matched strategy comparison.

## Reproduce

```sh
PYTHONPATH=src python3 experiments/trend_72h.py \
  --end 1792670400 \
  --data results/trend-72h-2026-10-22-1200-data.json \
  --output results/trend-72h-2026-10-22-1200.json
```

`--fee-each-side` and `--spread-bps` replay alternative scenarios from the saved data. The base output SHA-256 was identical on a second run. This is one short, in-sample exploratory window. Current allowlist applied to history creates survivorship bias; historical book depth/spread, slippage, actual account fee, credit fee, and exchange precision are unknown. This run calls **no AI model** and does not demonstrate a profitable trading edge or real-order feasibility.
