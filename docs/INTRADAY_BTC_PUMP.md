# BTC/PUMP intraday comparison, 20–22 October 2026

This is an **exploratory historical simulation**, not a live or AI trading result. Both BTCTMN and PUMPTMN were active spot markets and absent from the package-183 exclusion response when checked on 22 October 2026. The evaluated window is **2026-10-20 03:00 UTC to 2026-10-22 12:00 UTC** (57 hours; **20 October 06:30 to 22 October 15:30 Tehran**).

Wallex's public UDF endpoint rejected `resolution=5` and returned one-minute timestamps for `resolution=15` during this run. The experiment therefore downloads `resolution=1` in twelve-hour chunks and constructs 5m and 15m OHLCV bars only when *every* constituent minute exists. A shared thirteen-minute source gap (2026-10-19 23:08–23:20 UTC) forced a clean window after 2026-10-20 00:00 UTC; the three hours before the test are feature warm-up. The saved [minute candles](../results/intraday-btc-pump-1m-data.json) contain 7,187 rows per coin. All 720 five-minute and 240 fifteen-minute bars per coin in the clean period are complete; 684 and 228 of those, respectively, are in the 57-hour evaluation period.

## Rules fixed for this comparison

- **15m reversal:** after a red completed 15m candle, buy at the next open; sell at the following 15m open. This is the simplest long-only implementation of the reversal hypothesis. It can re-enter on successive red candles.
- **15m momentum + volume:** buy after the completed close exceeds the previous eight 15m highs, exceeds the close one hour earlier, and base volume is at least 1.5 times the prior twenty-bar median. Sell on a close below the previous four-bar low or after three hours, at the next open.
- **5m breakout:** buy after a close above the previous twelve 5m highs, a positive trailing-hour return, and volume at least 1.5 times the prior twenty-four-bar median. Sell on a close below the prior six-bar low or after one hour, at the next open.
- **5m EMA crossover:** buy when EMA(6) crosses above EMA(24) on completed closes. Sell when it crosses below or after two hours, at the next open.
- **BTC/PUMP 15m relative-strength rotation:** at each 15m open, compare each coin's *completed* trailing-hour return. Buy only the leader if its trailing-hour return exceeds 0.8%; sell after two hours or when it ceases to be the leader. This is a deterministic two-coin proxy for market ranking, **not** the trained random-forest strategy from the cited research and **not** an AI result.
- **Buy and hold:** buy once at the test start and liquidate at the final completed candle's close. Both 20% capital and near-100% capital variants are shown. The 20% variant matches the maximum concurrent allocation of the active strategies.

Each entry uses up to **20% of initial equity**, maximum one position per experiment. New buys pause after a **2% loss since the start of the Tehran calendar day**; a **5% loss from initial equity** closes the position at the next bar open and stops new buys. Signals use only completed candles. Entry and exit assume a **0.3% fee on each side**, plus a synthetic **20 basis-point full bid/ask spread** (10 bps paid at each side). The account's actual fee, historical spread/depth, slippage, and credit daily fee remain unverified. No actual orders are placed.

## Results after assumed costs

Returns and drawdowns are percentages of the **whole 5,000,000 TMN account**. Thus a 20% position that gains 10% before costs contributes about 2% to account return. “Trades” means completed round trips.

| Market | Strategy | Net return | Net P&L (TMN) | Max drawdown | Trades |
|---|---|---:|---:|---:|---:|
| BTC | Buy and hold, 20% | **+0.667%** | +33,374 | 0.639% | 1 |
| BTC | Buy and hold, near-100% | +3.327% | +166,371 | 3.049% | 1 |
| BTC | 15m reversal | −5.048% | −252,415 | 5.048% | 31 |
| BTC | 15m momentum + volume | −0.747% | −37,342 | 0.747% | 9 |
| BTC | 5m breakout | −3.668% | −183,415 | 3.668% | 23 |
| BTC | 5m EMA crossover | −1.404% | −70,185 | 1.518% | 13 |
| PUMP | Buy and hold, 20% | **+2.265%** | +113,267 | 1.824% | 1 |
| PUMP | Buy and hold, near-100% | +11.293% | +564,639 | 7.954% | 1 |
| PUMP | 15m reversal | −5.093% | −254,652 | 5.093% | 33 |
| PUMP | 15m momentum + volume | −0.896% | −44,794 | 1.627% | 7 |
| PUMP | 5m breakout | −2.909% | −145,464 | 2.909% | 20 |
| PUMP | 5m EMA crossover | −2.509% | −125,447 | 3.039% | 21 |
| BTC/PUMP | 15m relative-strength rotation | −3.551% | −177,530 | 3.596% | 27 |

The full [base-cost result](../results/intraday-btc-pump-57h.json) includes fee totals and trade logs. With **0.2% fee each side and the same 20 bps spread**, every active strategy still loses: see the [lower-fee replay](../results/intraday-btc-pump-57h-low-fee.json). At *zero* fee and *zero* spread, 15m BTC momentum earns +0.695%, below BTC's +0.831% buy-and-hold result at 20% allocation. The two-coin rotation earns +1.154%, below PUMP's +2.437% buy-and-hold result at 20% allocation; the [zero-cost replay](../results/intraday-btc-pump-57h-zero-cost.json) is a diagnostic, not a feasible execution scenario.

**Reading:** None of these fixed implementations beats 20% buy and hold in this 57-hour sample. The 15m momentum + volume version loses least among active BTC and PUMP rules under the base assumptions. The 15m one-candle reversal trades frequently and hits the 5% total stop on both coins. The two-coin rotation makes 27 round trips; assumed fees alone total roughly 161,953 TMN. This is evidence about these exact rules and this window, not proof that all intraday strategies fail or that buy and hold will win next time. No random-timing placebo or chronologically independent test is included here.

## Reproduce

From the repository root:

```sh
PYTHONPATH=src python3 experiments/intraday_two_coins.py
PYTHONPATH=src python3 experiments/intraday_two_coins.py --fee 0.002 --spread-bps 20 --output results/intraday-btc-pump-57h-low-fee.json
PYTHONPATH=src python3 experiments/intraday_two_coins.py --fee 0 --spread-bps 0 --output results/intraday-btc-pump-57h-zero-cost.json
```

The saved minute dataset is reused on subsequent runs; its SHA-256 appears in each result. To fetch fresh data, remove or change the `--data` path, noting that the script intentionally fixes this historical evaluation window. The historical book and executed prices are unavailable, so these results cannot establish live-order profitability. The rules were specified for this comparison after the window was known; this is an exploratory comparison, **not** a clean out-of-sample claim.
