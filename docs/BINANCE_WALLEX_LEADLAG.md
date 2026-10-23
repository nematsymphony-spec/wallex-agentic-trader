# BTC/PUMP: Binance–Wallex lead/lag, 1–5 minutes within an hourly context

## Outcome

The matched sample contains a positive association between a completed Binance one-minute return and the **next** Wallex one-minute return, for both BTC and PUMP. PUMP also shows a smaller association at a two-minute lag. Three-to-five-minute lags do not show a consistent positive relationship in the pointwise hourly-block bootstrap intervals. PUMP's largest correlation is contemporaneous: this does not mean its whole price response is delayed by one minute.

This is a historical **return correlation measurement**, not an execution backtest, causal finding, or demonstrated advantage over buy and hold.

## Data and alignment

- Window: **2026-10-20 03:00 UTC to 2026-10-22 00:00 UTC**, or **20 October 06:30 to 22 October 03:30 Tehran**, 45 hours. It starts with the earlier experiment but ends twelve hours earlier because the Binance daily file for 22 October was not yet available.
- Reference: Binance **spot BTCUSDT and PUMPUSDT** daily one-minute archives for 19–21 October. Public REST endpoints were unavailable from this environment; Binance's official public-data archive was accessible. PUMP is pump.fun in Binance's official spot listing.
- Target: saved Wallex BTCTMN/PUMPTMN one-minute trade candles. USDTTMN one-minute candles convert each local close into a USDT-equivalent close: `W_adjusted[t] = W_TMN[t] / USDT_TMN[t]`.
- There are 2,700 reference/test minutes and 2,700 local coin candles per market. USDTTMN has 310 missing minutes in this window, leaving 2,390 exact FX-matched closes. No missing minute is forward-filled. Return-pair counts are 2,389 for lags zero through five under the endpoint checks.
- Binance archive timestamps are microseconds; they are converted to UTC seconds and checked for minute alignment and missing test minutes. A candle stamped `t` opens at `t` and its close becomes available after `t+60`, not at the open.

Source documentation: [Binance public data](https://github.com/binance/binance-public-data/blob/master/README.md), [Binance PUMP spot listing](https://www.binance.bh/en-BH/support/announcement/detail/94ab969a74a8480799d03934b4720411).

## Method

Correlate **log returns**, not price levels:

`x[t] = log(B_close[t] / B_close[t-60])`

`y[t] = log(W_adjusted[t] / W_adjusted[t-60])`

`rho[k] = corr(x[t], y[t+60*k])`

Thus `k > 0` measures Binance leading Wallex, while negative `k` checks the reverse direction. Future local data outside the window are excluded. Returns require both exact endpoint observations. One-hour blocks of paired observations are resampled 1,500 times with a fixed seed to estimate 95% confidence intervals. These are **pointwise** intervals; they are not adjusted for selecting among lags or coins. No best lag was used to tune a trading strategy.

## Results, FX-adjusted target

| Binance lead | BTC correlation | BTC 95% interval | PUMP correlation | PUMP 95% interval |
|---|---:|---:|---:|---:|
| 0 minutes | 0.1147 | [0.0588, 0.1912] | 0.4535 | [0.3794, 0.5272] |
| 1 minute | **0.1484** | **[0.0906, 0.1880]** | **0.3002** | **[0.2466, 0.3498]** |
| 2 minutes | 0.0326 | [−0.0039, 0.0770] | **0.1166** | **[0.0671, 0.1594]** |
| 3 minutes | 0.0150 | [−0.0374, 0.0829] | −0.0100 | [−0.0563, 0.0425] |
| 4 minutes | 0.0355 | [−0.0365, 0.0980] | 0.0335 | [−0.0119, 0.0760] |
| 5 minutes | 0.0286 | [−0.0247, 0.0674] | −0.0392 | [−0.0819, 0.0049] |

A correlation of 0.3002 is **not** 30% return, 30% accuracy, or a guaranteed delay. It is the strength of a linear relationship between the paired return series. Candle publication timing, venue clock conventions, sparse trades, price rounding and bid/ask bounce can contribute to this relationship.

## Robustness checks

| Check, one-minute lag | BTC | PUMP |
|---|---:|---:|
| First half of evaluation | 0.1494 | 0.3099 |
| Second half | 0.1505 | 0.2909 |
| Both local return-endpoint candles have positive volume | 0.1823 (1,283 pairs) | 0.3445 (1,442 pairs) |
| Raw TMN return without FX adjustment | 0.1509 | 0.2965 |
| Reverse direction, Wallex leading by one minute | −0.0082 | −0.0001 |

The one-minute relationship survives these checks, so the measured association is not confined to zero-trade local candles or one half of the sample. This still does not establish executable profits or resolve candle timestamp/publication conventions.

## Connection to hourly decisions

At each UTC hour boundary, use the **previous five completed Binance minutes** as a directional diagnostic. Observe the local FX-adjusted return over the following 1–5 minutes and 60 minutes. Of 45 potential hourly observations, 39 have required FX observations. Only positive reference signals are summarized below to match a long-only setting.

| Coin | Positive hourly signals | Mean local forward 5m return after positive signal | Mean local forward 60m return after positive signal |
|---|---:|---:|---:|
| BTC | 19 | −0.0433% | +0.1402% |
| PUMP | 17 | −0.0001% | +0.6295% |

The unconditional mean 60m return on the same available hourly timestamps is +0.0913% for BTC and +0.3570% for PUMP. Positive reference signals condition on a small, post-hoc exploratory subset and are not an independent performance result. Even their average gross one-hour moves do not clear the earlier **approximately 0.8% round-trip cost assumption**. This is only a cost-hurdle illustration: actual TMN execution prices, entry/exit fees, trade overlap, risk stops, spreads and publication latency have **not** been simulated. These means are not portfolio returns, and a positive signal does not mandate a trade.

## Implication

For the next candidate model, use a one-minute lag for BTC and one-to-two-minute response windows for PUMP as **hypotheses to validate on new data**. Use exact timestamps, recently executed trades, FX adjustment and measured order latency. With hourly decisions, the last few minutes of reference-market information can be included as features. A trade should depend on predicted *remaining* local return after delay and all actual costs, not simply on Binance having risen. No trading implementation or live order was added in this experiment.

## Artifacts and reproduction

- [Analysis script](../experiments/cross_venue_leadlag.py)
- [Full measurements, bootstrap intervals and hourly observations](../results/cross-venue-leadlag-btc-pump.json)
- [Binance archive manifest](../results/binance-spot-1m/manifest.json)
- [Wallex USDTTMN source candles](../results/leadlag-wallex-usdt-1m.json)
- Coin source: `results/intraday-btc-pump-1m-data.json` from the prior experiment.

Run from the repository root with the saved source files:

```sh
PYTHONPATH=src python3 experiments/cross_venue_leadlag.py
```

Each Binance ZIP is an official daily archive at `https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-{DATE}.zip`. The sibling `.CHECKSUM` file supplies its SHA-256. ZIP hashes, local input hashes and the deterministic bootstrap seed allow replay. Results are based on one 45-hour historical window with no independent validation and no historical order book.
