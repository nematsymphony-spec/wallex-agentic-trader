# Two-minute remaining-movement strategy: executable timing

## Implemented behavior

The hourly prediction head has been removed from this candidate. For each of BTC/TMN and PUMP/TMN, independently:

- Evaluate the latest complete feature snapshot every minute.
- Buy with **all available cash**, reserving the entry fee, only if forecast gross log return exceeds the exact fee/spread hurdle plus a fixed **20 bps buffer**.
- Simulate a **60-second order delay**. Fill at the local open after that delay, not at the earlier signal price.
- Hold for at most **120 seconds after the fill**. A nonpositive or missing/invalid subsequent forecast schedules a sell with the same delay; the mandatory time exit remains in effect.
- Permit only one position or pending entry at a time. Gains/losses compound into the next all-in entry.
- Pause new buys after a 2% Tehran-calendar-day loss; stop and close at the next minute open after a 5% loss from initial capital. Losses can overshoot the threshold.
- Log each base-cost decision, feature snapshot reference, forecast, threshold, execution and reason.

Each coin experiment starts with an independent **5,000,000 TMN** account. The simulation does not allocate 100% of one account to two coins simultaneously. There is no exchange order submission or change to production account settings.

## Why the prediction label had to change

The earlier model predicted two-minute **FX-adjusted close-to-close movement from decision time**. If entry takes one minute, a new two-minute position spans decision+1m to decision+3m. That is a different outcome, in actual TMN rather than FX-adjusted prices.

Two implementations are saved to make this distinction reviewable:

1. **Frozen original model replay:** reuse the saved two-minute weights without refitting. Measure delayed execution separately. On delayed TMN outcomes, its relative MSE improves 6.89% for BTC but becomes 10.23% worse for PUMP than zero forecasting.
2. **Execution-aligned two-minute candidate:** retain the same ten causal features, per-coin ridge regression, fixed penalty 10 and chronological splits. Change only the training label to actual Wallex TMN open-to-open return from **decision+60s to decision+180s**. This is the candidate that matches the implemented holding period.

Training is before 19 October 00:00 UTC; validation is 19 October 00:00–20 October 03:00 UTC; evaluation is **20 October 03:00–22 October 00:00 UTC** (45 hours; **20 October 06:30–22 October 03:30 Tehran**). Labels crossing a boundary are purged. Final diagnostic models use only labels ending before evaluation. The evaluation window was already examined for lead/lag discovery; it is not untouched new data. No hyperparameter or trading-threshold search was performed.

## Execution-aligned prediction results

| Metric | BTC | PUMP |
|---|---:|---:|
| Validation reduction in MSE vs zero forecast | **−2.17%** | +2.11% |
| Evaluation reduction in MSE vs zero forecast | +6.23% | **−0.94%** |
| Evaluation prediction/actual correlation | 0.2539 | 0.0593 |
| Correct direction on nonzero evaluation targets | 58.76% | 55.42% |
| Always-up correct direction on the same targets | 51.41% | 52.78% |
| Largest positive forecast in evaluation | 0.231% | 0.263% |
| Candidate status | Rejected in validation | Rejected in evaluation |

There are 2,326 evaluation labels per coin, with overlapping targets. These are not independent trials. The earlier 64–66% direction results do not carry over to the delayed actual-execution target. Correct direction is not a trading win rate.

## All-in trading results

The base scenario assumes **0.3% fee on each side** and a synthetic **20 bps full spread**, approximately 0.8% total round-trip friction. The fixed 20 bps buffer raises the entry hurdle to about **1.005% forecast gross return**. Actual account maker/taker fees are still unverified.

| Scenario, execution-aligned candidate | BTC return / trades | PUMP return / trades |
|---|---:|---:|
| 0.3% fee each side + 0.2% full spread | 0.000% / 0 | 0.000% / 0 |
| 0.2% fee each side + 0.2% full spread | 0.000% / 0 | 0.000% / 0 |
| 0.1% fee each side + 0.2% full spread | 0.000% / 0 | 0.000% / 0 |
| Zero fee + 0.2% full spread | 0.000% / 0 | 0.000% / 0 |
| Zero fee and zero spread, diagnostic | +0.269% / 1 | +0.459% / 7 |
| Buy and hold, base-cost scenario | **+5.013% / 1** | **+16.710% / 1** |

The buffer remains 20 bps in every scenario. The zero-cost diagnostic is not an executable fee claim. No entries pass the base-cost hurdle, and both candidates have failed a model-evaluation gate; neither is accepted for live deployment.

For comparison, the **frozen original** model, with no retargeting, produces 0% and no entries at base cost. Its zero-fee/zero-spread, 60-second-delay replay earns +0.611% BTC (two trades) and +7.358% PUMP (127 trades). Removing the delay as well gives −0.209% BTC and +35.017% PUMP. Those are optimistic diagnostics using a mismatched target and unknown historical execution conditions, not evidence of an achievable return. They show how assumptions about delay and cost can dominate the attractive prediction result.

## What this establishes

The minute-cadence, two-minute holding implementation now exists and can be replayed from saved models and data. Under the tested assumptions, the predicted residual move is too small to fund a trade. Retargeting to delayed execution also weakens the apparent predictive edge. The next practical inputs are the account's **actual maker/taker fees**, measured data/order latency, and new data with the candidate frozen. This experiment does not establish a profitable strategy or beat buy and hold.

Historical depth, spread, queue position, cancellation timing and actual publication delay are unavailable. The simulator uses trade-candle opens with assumed spread, full fills and no additional slippage. Credit daily fees are omitted. Minute close valuations can miss intraminute drawdowns. The short model's failed validation/evaluation status remains explicit in its results.

## Artifacts and reproduction

- [Execution-aligned models](../results/execution-aligned-two-minute-models.json)
- [Execution-aligned metrics, scenarios and decisions](../results/execution-aligned-two-minute-btc-pump.json)
- [Frozen-model replay](../results/two-minute-execution-btc-pump.json)
- [Execution simulation](../experiments/two_minute_strategy.py)
- [Training with execution-aligned labels](../experiments/execution_aligned_two_minute_model.py)
- [Integrity tests](../experiments/test_two_minute_strategy.py)

```sh
python3 -m pip install -r experiments/requirements.txt
PYTHONPATH=src python3 experiments/two_minute_strategy.py
PYTHONPATH=src python3 experiments/execution_aligned_two_minute_model.py
PYTHONPATH=src python3 -m unittest discover -s experiments -p 'test_two_minute_strategy.py' -v
```

Saved source data, model cutoffs and input hashes permit replay. Tests check the independently calculated all-in fee formula, exact delayed entry and holding duration, use of the execution price rather than signal price, rejection of invalid/weak signals, and purging of execution labels at split boundaries.
