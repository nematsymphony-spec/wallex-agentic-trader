# Trained prediction of Wallex's remaining movement

## Outcome

The two-minute ridge models improve squared prediction error on the chronological evaluation period and outperform an ablation using only Wallex/FX data. The 60-minute models fail validation and evaluation. No hourly entry passes the combined short-signal, estimated-cost and risk gates under the base assumptions. Both hourly trading candidates are **rejected for deployment**. The base simulation remains in cash and earns 0%, versus +5.013% BTC and +16.710% PUMP buy and hold over this experiment's 45-hour window.

This is a reproducible offline model experiment, not a demonstrated live trading edge. The test period was already examined during the preceding lead/lag investigation. Its separation is chronological with respect to model fitting, but it is **not an untouched research holdout**.

## Model and input

Separate models are fitted for BTC and PUMP, with two distinct prediction heads:

1. **Two-minute remaining movement:** future two-minute log return of Wallex's TMN price divided by simultaneous USDTTMN. This removes FX changes from the measured catch-up.
2. **One-hour executable-price movement:** log return between the Wallex open one minute after a decision and the open sixty minutes after that entry. This target is in TMN, since the account actually trades TMN.

Each head is a linear ridge regression with fixed penalty 10. Inputs are standardized with means/scales fitted on the training observations only. No hyperparameter search is performed. The ten features are the latest and preceding one-minute Binance returns; the corresponding local USDT-equivalent returns; current relative discount compared with the prior hour's median cross-venue basis; latest USDTTMN return; local and reference one-hour returns; local one-hour realized volatility; and current local volume relative to the prior-hour median.

The relative-discount feature accounts for a persistent premium between venues. A Binance rise already matched by Wallex should not by itself imply that another local rise remains. Including local returns lets the model distinguish movement already reflected locally from potential remaining adjustment. Model coefficients are learned from data rather than hand-assigned.

Exactly 61 consecutive historical minute endpoints are required for a feature snapshot; gaps are not filled. A minute candle stamped `t` closes and becomes usable at `t+60`. Feature computation uses nothing after `t`. The two-minute label is available at `t+180`. An hourly trading decision at `t+60` uses the local entry open at `t+120`, allowing one minute of execution delay. Actual publication and network delays cannot be measured retrospectively from candles.

## Chronological splits and checks

| Partition | UTC interval | Purpose |
|---|---|---|
| Initial training | 17 October 12:00 to 19 October 00:00 | Fit fixed ridge models and scaling |
| Validation | 19 October 00:00 to 20 October 03:00 | Check against a zero-return forecast |
| Evaluation | 20 October 03:00 to 22 October 00:00 | 45-hour historical comparison |

Labels ending at or after a split boundary are excluded. After validation, models fitted on all eligible pre-evaluation observations are replayed for diagnostics. A head must improve validation MSE relative to a zero-return forecast to pass the model gate. The two-minute heads pass; the one-hour heads do not. The failed one-hour heads are retained only to show the diagnostics and are not accepted for deployment.

Sources are Wallex cached coin and USDTTMN minute candles plus official Binance spot daily archives for 17–21 October. The four additional Binance training ZIPs and six prior ZIPs have verified official SHA-256 checksums. The missing FX/history intervals reduce the available sample. For each coin, two-minute training/validation/evaluation counts are 2,097 / 1,310 / 2,327. The one-hour counts are 2,038 / 1,312 / 2,268. Adjacent target windows overlap; row counts are not independent trial counts.

## Forecast results

The baseline “zero” predicts no price movement. `R² vs zero = 1 − model MSE / zero-forecast MSE` is relative to that baseline, not conventional mean-centered R².

| Two-minute evaluation | BTC | PUMP |
|---|---:|---:|
| Model/actual correlation | 0.4426 | 0.4315 |
| Reduction in MSE versus zero forecast | 18.64% | 18.75% |
| Local-only reduction in MSE versus zero | 5.76% | −1.53% |
| Correct direction, nonzero targets | 64.03% | 66.37% |
| Local-only correct direction | 57.47% | 49.63% |
| Always-up correct direction | 52.95% | 50.87% |
| RMSE | 9.54 bps | 26.93 bps |
| Zero-forecast RMSE | 10.57 bps | 29.88 bps |
| MAE | 5.79 bps | 19.13 bps |
| Zero-forecast MAE | 5.49 bps | 20.35 bps |
| Largest predicted positive two-minute return across eligible evaluation minutes | 0.460% | 0.609% |

BTC's mean absolute error is slightly worse than the zero forecast despite the improved squared error. Thus the improvement does not hold for every error measure. The reported direction accuracies exclude flat targets and are not trading win rates or independent binomial trials.

For the **one-hour head**, MSE is **6.12% worse than zero** on BTC and **19.76% worse** on PUMP. Prediction/actual correlations are −0.0240 and −0.2439. Validation MSE was also worse: 42.63% and 28.88%, respectively. The short-lived information does not support deploying this one-hour predictor.

## All-in hourly simulation

Hourly decisions use each head's corresponding forecast. A hypothetical buy requires both a positive two-minute forecast and a one-hour forecast exceeding the exact round-trip cost ratio plus a 20 bps log-return buffer. Base costs are 0.3% per side plus a synthetic 20 bps full spread, approximately 0.8% round trip. The entry threshold is about 1.005% gross predicted return with the buffer. The feature timestamp, prediction values, threshold and rejection reason appear in every decision log.

Entry invests all current cash, reserving the entry fee; each coin has an independent 5,000,000 TMN account. Positions exit after one hour or earlier on a 5% initial-capital loss checked on minute closes, executing the stop at the following minute's open. New buys pause on a 2% Tehran-calendar-day loss. Pure buy and hold starts at the same delayed entry open and liquidates at the same final completed close, without interim stops.

| Coin | Base strategy return | Round trips | Buy-and-hold return | Buy-and-hold P&L |
|---|---:|---:|---:|---:|
| BTC | 0.000% | 0 | +5.013% | +250,654 TMN |
| PUMP | 0.000% | 0 | +16.710% | +835,482 TMN |

Of 45 hourly decisions per coin, seven lack contiguous input history, twenty-four have nonpositive short forecasts, and fourteen fail the one-hour cost threshold. Both candidates also fail the one-hour validation gate. Those gate counts describe a hypothetical replay of a rejected model, not a ready-to-trade algorithm.

The largest short-horizon forecasts, even before allowing for execution delay and liquidity, remain below the approximately 0.8% base round-trip assumption. A zero-fee/zero-spread diagnostic, retaining the same 20 bps buffer, gives +0.253% BTC (one trade) and +5.416% PUMP (nine trades); these runs use a rejected one-hour model and do not establish feasibility. Historical spreads, book depth, queue positions, extra slippage, actual account trading fees and daily credit fees are not included.

## Artifacts and reproduction

- [Model parameters, scaling and feature names](../results/remaining-move-models.json)
- [Metrics, validation gates, hourly decisions and trade logs](../results/remaining-move-btc-pump.json)
- [Experiment implementation](../experiments/remaining_move_model.py)
- [Integrity tests](../experiments/test_remaining_move_model.py)

```sh
python3 -m pip install -r experiments/requirements.txt
PYTHONPATH=src python3 experiments/remaining_move_model.py
PYTHONPATH=src python3 -m unittest discover -s experiments -p 'test_remaining_move_model.py' -v
```

Run from the repository root with cached data present. Research scripts require NumPy; the production trader has no added dependency. Models, features, dataset hashes, NumPy/Python versions and every hourly feature snapshot are saved. Tests verify that changing future local prices cannot change current features, labels crossing split boundaries are purged, and a synthetic all-in round trip exactly matches an independent fee/spread formula.

The appropriate next evaluation is on newly arriving data with the two-minute model frozen. Existing historical diagnostics support further testing of the short head, while the hourly candidate remains rejected. No live order or exchange account mutation was performed.
