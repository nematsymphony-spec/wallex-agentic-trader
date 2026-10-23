# Observed package-183 account fees

On **23 October 2026 (Tehran date)**, the user's authenticated Wallex Chrome session showed these rates in the **trade-credit dashboard** for the **هکاتون استارکوچ | والکس** package:

| Quote currency | Maker fee each side | Taker fee each side |
|---|---:|---:|
| TMN | **0.25%** | **0.30%** |
| USDT | **0.10%** | **0.13%** |

The same page showed the **three-day credit fee as 0 TMN** and accrued credit fee to date as **0 TMN**. This is distinct from transaction fees, which are not zero. No account setting or order was changed.

Source: [authenticated credit dashboard](https://wallex.ir/app/trade-credit/dashboard). [Machine-readable observation](../config/package-183-fees-observed.json).

## Effect on the saved experiments

For BTC/TMN and PUMP/TMN, the experiments' **0.3% per-side taker rate is now confirmed as the current account rate**. Their synthetic 20 bps full spread remains an assumption. Approximately 0.6% taker fees plus 0.2% assumed spread still gives the roughly 0.8% round-trip hurdle used in those tests, before their extra entry buffer. No base-case recalculation is needed to reflect the observed taker rate because the number is identical.

Two maker executions would charge approximately 0.5% in transaction fees alone. They cannot be simulated by merely changing the taker fee: passive orders have different fill prices, queue uncertainty, adverse selection and cancellation behavior. The dashboard provides no maker rebate; the displayed maker coefficients are positive fees.

USDT markets have lower transaction fees, but the existing account and experiment capital are TMN. USDT-funded experiments would also need to account for conversion, USDT-denominated prices, allowed-market checks and any required final TMN conversion. The TMN experiment's synthetic spread must not be reused as an observed USDT spread.

These rates describe the account as observed on 23 October. Earlier experiment documents honestly describe the information available when they were generated; this note updates that uncertainty without asserting the rates applied on all historical data dates. Fees should be checked again before any eventual live execution.
