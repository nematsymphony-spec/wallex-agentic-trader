# Test results (2026-10-22)

## Historical engineering baseline: original three markets

- Window: 2026-10-18 00:00 UTC through 2026-10-21 00:00 UTC, BTC/ETH/SOL versus TMN, hourly bars.
- Rule model: **not AI**. 216 decisions, 12 simulated fills.
- Starting equity: 5,000,000 TMN; ending equity: **4,954,411.87 TMN**.
- Net return: **−0.9118%**; maximum mark-to-market drawdown: **0.9118%**.
- Assumptions: 20 bps round-trip bid/ask spread at each reference price, 0.3% taker fee on each side, full fill at synthetic next-bar opening bid/ask, synthetic sufficient depth. The historical exchange order book was not available. These results do not establish profitability.
- Raw results: [`results/baseline-final.json`](../results/baseline-final.json). Example audit sequence: [`results/example-trace.json`](../results/example-trace.json). Full log: local SQLite file `results/baseline-final.db` (excluded from Git because it can contain private strategy traces).

## Expanded candidate universe: PUMP/TMN

On the **same historical window and cost assumptions**, adding PUMPTMN to the rule-model candidate list gave 288 decisions and 12 simulated fills. Starting equity was 5,000,000 TMN; ending equity was **5,013,261.50 TMN** (**+0.2652%**), with **0.8691%** maximum drawdown. Three of the twelve fills were PUMPTMN. See [`results/baseline-four-markets.json`](../results/baseline-four-markets.json).

This is an engineering comparison, not evidence of a reliable PUMP edge: the backtest assumes constant spread, sufficient historical depth, full fills, and fractional quantities even where exchange precision may differ. Current order-book liquidity can change sharply. The AI was not involved in either baseline.

## Shadow smoke test

- Public Wallex API returned all four current candidate markets active and absent from package 183's exclusion list.
- One AI shadow cycle observed all four markets. `OPENAI_API_KEY` was not configured, so each decision safely returned `HOLD`; **no AI efficacy or trading P&L was measured**.
- Raw result: [`results/shadow-openai.json`](../results/shadow-openai.json).
- Audit chain verification: valid.

## Automated checks

Nine `unittest` cases pass: credit market exclusion, capital/position/drawdown limits, spread and thin-exit-book filters, liquidity-based position sizing, malformed AI response fail-closed, duplicate/chain audit behavior, next-bar opening fill, and disabled live command.
