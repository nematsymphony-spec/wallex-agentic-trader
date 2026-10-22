# Dynamic market selection

The first prototypes considered BTC, ETH, SOL and then PUMP. The current selector discovers every active TMN spot market allowed by credit package 183. Package exclusions come from Wallex's live response **unioned with** the [local 357-market exclusion snapshot](../config/package-183-exclusions.json) supplied for this challenge. Any additional live exclusions are also forbidden. The market list must be refreshed each cycle; an API failure stops the cycle.

At the 2026-10-22 sample, this left 62 TMN and 58 USDT pairs. All 58 allowed USDT base assets also had an allowed TMN pair. With TMN-denominated credit and no verified USDT credit balance, the 62 TMN pairs cover the distinct currently allowed assets without a conversion trade. The live catalog can change.

For each TMN market, six non-overlapping blocks of 12 hourly percentage returns are measured from 73 consecutive completed hourly closes. Each block's realized volatility is `sqrt(sum(return²))`; adjacent blocks average into a day. The newest three days receive 50%, 30% and 20%. Percentage returns make different price scales comparable. Missing hourly bars or stale candles exclude the market.

Admission then requires: positive valid bid/ask, spread no wider than 35 bps, at least 500,000 TMN within the first five levels **on each side** of the book, and at least 50 million TMN estimated 24-hour quote volume. The score combines volatility percentile 25%, trend percentile 35%, volume percentile 20%, and a spread-cost score 20%. Thus volatility is a bounded prioritization factor and cannot override eligibility. The AI sees all eligible markets and current holdings together in one decision, and may choose one `BUY`, one position-reducing `SELL`, or `HOLD`. Risk controls independently veto the action.

The first full-universe engineering baseline returned **−1.4952%** under synthetic costs and depth. It is a rule-model test, not an AI result or evidence that high volatility creates an edge. See [results](RESULTS.md) for the assumptions and raw files.
