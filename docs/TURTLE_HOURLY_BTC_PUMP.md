# Hourly Turtle adaptations: BTC and PUMP

## Design

Offline replay over the same frozen comparison window: **2026-10-20 03:00 UTC to 2026-10-22 12:00 UTC** (20 October 06:30 to 22 October 15:30 Tehran), 57 hours. There are 168 preceding hourly candles for warmup. This is not a fresh last-three-days download. Each coin has a separate initial 5,000,000 TMN account; every entry uses all available cash after reserving its fee.

Source: [The Original Turtle Trading Rules](https://www.tradingwithrayner.com/wp-content/uploads/2014/11/OriginalTurtleRules.pdf), published by original Turtle Curtis Faith. The original uses daily bars, intraday breakouts, long/short futures, volatility-sized units and pyramiding. This experiment instead uses **hourly close confirmation, next-hour-open fills, spot longs and all-in sizing without pyramiding**. Results evaluate that specific adaptation, not the unmodified historical system.

## Rules fixed before running

- S1: close above the preceding 20 hourly highs; close below preceding 10 lows to exit. Track virtual long AND short 20/10 breakouts, including skipped ones, to skip the next breakout after a gross winning virtual trade. A 55-hour breakout remains a failsafe entry, with a 10-hour exit in this adaptation. Start virtual state at hour 56 of warmup, assuming the preceding breakout was a loser.
- S2: close above preceding 55 hourly highs; close below preceding 20 lows to exit; no previous-winner filter.
- Signals exclude their own candle from the channel. Execute after the signal candle finishes, at the next hourly open.
- N uses true range, a 20-bar average seed and recursion (19*N_previous + TR)/20. Entry stop = actual entry fill minus 2N, fixed for the trade. An intrahour low touching the stop exits there; if the next open gaps through, exit at that worse open. No same-hour re-entry after an exit.
- Preserve previous portfolio guards: pause fresh buys after 2% Tehran-day loss; close at next open and disable buys after 5% loss versus initial capital. Hourly observation can overshoot. Pure buy and hold has no interim stops.
- Base costs: current account taker fee **0.3% each side**, assumed full spread **20 bps**, credit package fee **0 TMN**. Fee evidence is in `config/package-183-fees-observed.json`, observed 23 October. Historical replay uses those current rates. Spread is synthetic, not measured history. No depth, rounding, minimum-order or extra slippage modeling.

## Results

| Coin | Rule | Net return | Net P&L TMN | Hourly close max drawdown | Round trips | Wins |
|---|---|---:|---:|---:|---:|---:|
| BTCTMN | Buy and hold | +3.327% | +166,371 | 2.947% | 1 | 1 |
| BTCTMN | S1 20/10 + skip filter + 55h failsafe | -4.951% | -247,551 | 4.951% | 3 | 0 |
| BTCTMN | 20/10 without skip filter (control) | -4.951% | -247,551 | 4.951% | 3 | 0 |
| BTCTMN | S2 55/20 | -1.678% | -83,878 | 3.619% | 1 | 0 |
| PUMPTMN | Buy and hold | +11.389% | +569,452 | 6.676% | 1 | 1 |
| PUMPTMN | S1 20/10 + skip filter + 55h failsafe | -5.091% | -254,569 | 5.091% | 2 | 0 |
| PUMPTMN | 20/10 without skip filter (control) | -5.091% | -254,569 | 5.091% | 2 | 0 |
| PUMPTMN | S2 55/20 | -5.091% | -254,569 | 5.091% | 2 | 0 |

The filtered and unfiltered 20/10 controls give identical actual trades in this window; they are not independent evidence. PUMP S1 and S2 also selected the same actual trades before the portfolio guard halted both.

## Why the losses occurred

All three BTC S1 trades hit their fixed 2N stops. BTC S2 selected only the final of those three trades, also stopped out. PUMP entered after an upward breakout, stopped out in the entry hour, then entered a second breakout after the Tehran day reset. The initial-equity loss threshold was breached and the second trade was closed at the next hourly open. Its final loss exceeded 5% because checking and executing happen at hourly boundaries. Later upward signals were blocked by the latched portfolio stop. These are observed mechanisms in this replay; they do not establish that the Turtle rules fail generally.

## Zero-cost diagnostic

| Coin | S1 20/10 filtered | S2 55/20 | Buy and hold |
|---|---:|---:|---:|
| BTCTMN | -2.937% | -0.988% | +4.157% |
| PUMPTMN | -0.170% | +0.684% | +12.284% |

Costs are not the sole reason for underperformance: both variants still trail buy and hold with zero costs. This is a separate simulation: costs can change stop activation and later trade counts, so subtracting the two return columns does not isolate only fee drag.

## Interpretation and limits

These hourly all-in variants did not beat buy and hold on either coin. The sample contains only 1–3 round trips per rule in one 57-hour window, with no held-out test or random-entry placebo. It cannot establish a durable edge or disprove the original daily diversified Turtle system. The all-in size removes volatility-adjusted capital sizing, a central part of the original method. Drawdown is sampled at hourly closes and may omit worse intrahour equity values. Stops assume executable liquidity at the modeled price. Historical minute/hour endpoints can differ slightly; the hourly benchmark is reused consistently here.

## Reproduce

```sh
PYTHONPATH=src:experiments python3 experiments/turtle_hourly.py
PYTHONPATH=src:experiments python3 -m unittest discover -s experiments -p test_turtle_hourly.py -v
```

- Data: `results/hourly-btc-pump-data.json`
- Results, input SHA-256, rules, decisions, virtual breakouts and actual trade logs: `results/turtle-hourly-btc-pump-57h.json`
- Code: `experiments/turtle_hourly.py`
- Verification: four checks passed for channel causality, N seed/recursion, next-open/gap fills and all-in fee accounting, and future-data invariance.

No AI decisions or exchange orders were used. No parameters were optimized against this evaluation window.
