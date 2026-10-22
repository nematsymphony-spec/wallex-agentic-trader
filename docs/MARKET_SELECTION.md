# PUMP market check, 2026-10-22

The first prototype limited candidates to BTC, ETH and SOL to keep the implementation small. This was a design choice, not a restriction of package 183. The live public Wallex responses showed **PUMPTMN and PUMPUSDT both active for spot and absent from package 183's `unavailableMarkets` list**. Eligibility must be rechecked before any future order.

In one snapshot, PUMPTMN showed approximately **15.4 bps spread**, 97.8 million TMN on the first five bid levels, and 3.0 million TMN on the first five ask levels. The three most recent non-overlapping 24-hour realized volatility estimates were about 5.7%, 8.2%, and 4.3%; a 50/30/20 weighting gave **6.2%**, versus roughly 2.0% for BTC, 2.5% for ETH and 3.0% for SOL. These figures change continuously.

PUMPUSDT was also allowed, but the sampled spread was approximately **102 bps**, before USDT trading fees or slippage. It was not added to the first expanded candidate set.

The PUMPTMN ask depth in that snapshot would support at most about **600,000 TMN** under the prototype's five-times-depth rule, below the normal 750,000 TMN target. The code now scales the intended buy down to the smaller of the target, capital limit, and both sides' depth divided by five. High volatility increases ranking interest, but it does not override liquidity, trend, fee and drawdown checks.

The current AI still evaluates each symbol separately; a later candidate-selection revision can compare all eligible symbols in one structured response and choose one or `HOLD`. That design should be tested against the existing per-symbol baseline without tuning on the evaluation period.
