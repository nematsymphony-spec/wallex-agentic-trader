# Hourly BTC/PUMP comparison with all available capital

For subsequent experiments in this conversation, the user selected **BTC/TMN and PUMP/TMN**, **buy and hold as the benchmark**, and **all currently available cash on every entry**, including accumulated gains or losses. Each coin is tested in a separate hypothetical 5,000,000 TMN account; the results do not assume two simultaneous all-in positions. The two-coin rotation also holds only one coin at a time.

The hourly evaluation uses exactly the previous intraday test's **57-hour window: 20 October 2026 03:00 UTC to 22 October 2026 12:00 UTC** (20 October 06:30 to 22 October 15:30 Tehran). Seven days of preceding hourly candles warm up the features. Wallex `resolution=60` supplies 225 complete hourly candles per coin, including 168 warm-up hours and 57 evaluation hours. No parameter search was performed.

## Common assumptions

- Entry invests all available cash after reserving the buy-side fee. A round trip compounds gains or losses before the next entry.
- Fee is **0.3% each side** and synthetic full spread is **20 bps**, approximately 0.8% combined round-trip cost before any extra slippage.
- Strategies pause new buys after a 2% loss since the Tehran day's opening valuation. A loss of 5% against initial capital closes a held position at the next hourly open and prevents further buys. This is an initial-capital loss threshold, not a trailing drawdown limit. Hourly checking can overshoot it.
- Pure buy and hold is bought once and sold at the evaluation end; it has no interim stops.
- Signals use completed candles and execute at the next hourly open. Terminal liquidation uses the last completed close. No historical book depth, extra slippage, account-specific trading fee, or credit daily fee is available.
- The 5m/15m strategies were rerun with all available capital to compare position sizes consistently. See [all-in intraday results](../results/intraday-btc-pump-57h-all-in.json).

## Translation of rules to hourly candles

The lookback and holding limits preserve the earlier rules' **number of candles**, so the economic horizons grow when moving to one-hour candles.

| Strategy | Hourly entry | Hourly exit |
|---|---|---|
| Reversal | Previous completed candle is red | After one hour |
| Momentum + volume | Close exceeds previous 8 highs; positive 4-hour return; volume at least 1.5× prior 20-bar median | Close below previous 4 lows, or after 12 hours |
| Breakout | Close exceeds previous 12 highs; positive 12-hour return; volume at least 1.5× prior 24-bar median | Close below previous 6 lows, or after 12 hours |
| EMA crossover | EMA(6) crosses above EMA(24) | Cross below, or after 24 hours |
| Two-coin rotation | Select higher completed 4-hour return, provided it exceeds 0.8% | Leader changes, threshold fails, or after 8 hours |

These are deterministic rule implementations. Rotation is a proxy for relative-strength selection, not a trained AI model.

## Hourly results after assumed costs

| Market | Strategy | Net return | Net P&L (TMN) | Max drawdown* | Round trips |
|---|---|---:|---:|---:|---:|
| BTC | Buy and hold | **+3.327%** | +166,371 | 2.947% | 1 |
| BTC | Reversal | −5.196% | −259,790 | 5.220% | 6 |
| BTC | Momentum + volume | +0.769% | +38,434 | 2.470% | 3 |
| BTC | Breakout | +0.769% | +38,434 | 2.470% | 3 |
| BTC | EMA crossover | +0.745% | +37,245 | 1.295% | 2 |
| PUMP | Buy and hold | **+11.389%** | +569,452 | 6.676% | 1 |
| PUMP | Reversal | −5.471% | −273,561 | 5.471% | 5 |
| PUMP | Momentum + volume | +6.544% | +327,187 | 4.143% | 3 |
| PUMP | Breakout | +6.544% | +327,187 | 4.143% | 3 |
| PUMP | EMA crossover | 0.000% | 0 | 0.000% | 0 |
| BTC/PUMP | Two-coin rotation | −6.166% | −308,318 | 7.942% | 7 |

*Drawdown here uses hourly close valuations and includes hypothetical liquidation costs. Intrahour drawdowns may be larger. The earlier minute-derived buy-and-hold PUMP entry open was 1,157 TMN, whereas the hourly endpoint reports 1,156 TMN at the same timestamp; terminal prices agree. Consequently its minute-derived benchmark was +11.293% rather than +11.389%. BTC entry and terminal prices agree. This small source discrepancy and different drawdown sampling should be considered when comparing timeframes.

Momentum and breakout yield identical hourly returns because **both rules selected the same three entries and exits on each coin in this window**. They are not two independent confirmations. EMA did not generate a fresh upward crossover for PUMP within the test window, so it remained in cash.

## All-in timeframe comparison

| Strategy | Earlier BTC 5m/15m | Hourly BTC | Earlier PUMP 5m/15m | Hourly PUMP |
|---|---:|---:|---:|---:|
| Reversal | −5.321% (15m) | −5.196% | −5.788% (15m) | −5.471% |
| Momentum + volume | −3.960% (15m) | +0.769% | −3.452% (15m) | +6.544% |
| Breakout | −5.607% (5m) | +0.769% | −5.364% (5m) | +6.544% |
| EMA crossover | −5.548% (5m) | +0.745% | −4.589% (5m) | 0.000% |
| Two-coin rotation | −5.752% (15m, combined account) | −6.166% (combined account) | — | — |

Hourly momentum/breakout improves on the tested intraday variants but remains **2.559 percentage points behind BTC buy and hold** and **4.845 points behind PUMP buy and hold**. PUMP momentum/breakout also shows lower hourly-sampled drawdown than PUMP buy and hold. Three trades per coin are insufficient to establish a repeatable edge or an advantage over a random-timing baseline.

## Cost sensitivity

| Market / rule | Base: 0.3% fee/side + 20 bps spread | 0.2% fee/side + 20 bps spread | Zero fee and zero spread |
|---|---:|---:|---:|
| BTC buy and hold | +3.327% | +3.534% | +4.157% |
| BTC momentum/breakout | +0.769% | +1.375% | +3.216% |
| PUMP buy and hold | +11.389% | +11.612% | +12.284% |
| PUMP momentum/breakout | +6.544% | +7.185% | +9.132% |

Even at zero execution cost, the tested momentum/breakout rules do not beat buy and hold in this window. Zero-cost results are diagnostics; risk controls can cause different trade counts in other rules when costs change.

## Saved artifacts and reproduction

- [Hourly source data](../results/hourly-btc-pump-data.json)
- [Base results and trade logs](../results/hourly-btc-pump-57h-all-in.json)
- [Lower-fee replay](../results/hourly-btc-pump-57h-all-in-low-fee.json)
- [Zero-cost replay](../results/hourly-btc-pump-57h-all-in-zero-cost.json)
- [Experiment script](../experiments/hourly_two_coins.py)

```sh
PYTHONPATH=src python3 experiments/hourly_two_coins.py
PYTHONPATH=src python3 experiments/hourly_two_coins.py --fee 0.002 --output results/hourly-btc-pump-57h-all-in-low-fee.json
PYTHONPATH=src python3 experiments/hourly_two_coins.py --fee 0 --spread-bps 0 --output results/hourly-btc-pump-57h-all-in-zero-cost.json
PYTHONPATH=src python3 experiments/intraday_two_coins.py --allocation 1 --output results/intraday-btc-pump-57h-all-in.json
```

The fixed historical data are cached and their SHA-256 appears in each result. This is one exploratory historical period, with assumed execution costs and no historical liquidity simulation, no AI inference, and no independent out-of-sample or random-timing test.
