# Test results (2026-10-22)

## Historical engineering baseline

- Window: 2026-10-18 00:00 UTC through 2026-10-21 00:00 UTC, BTC/ETH/SOL versus TMN, hourly bars.
- Rule model: **not AI**. 216 decisions, 12 simulated fills.
- Starting equity: 5,000,000 TMN; ending equity: **4,954,411.87 TMN**.
- Net return: **−0.9118%**; maximum mark-to-market drawdown: **0.9118%**.
- Assumptions: 20 bps round-trip bid/ask spread at each reference price, 0.3% taker fee on each side, full fill at synthetic next-bar opening bid/ask, synthetic sufficient depth. The historical exchange order book was not available. These results do not establish profitability.
- Raw results: [`results/baseline-final.json`](../results/baseline-final.json). Example audit sequence: [`results/example-trace.json`](../results/example-trace.json). Full log: local SQLite file `results/baseline-final.db` (excluded from Git because it can contain private strategy traces).

## Shadow smoke test

- Public Wallex API returned all three target markets active and absent from package 183's exclusion list.
- One AI shadow cycle observed all three markets. `OPENAI_API_KEY` was not configured, so each decision safely returned `HOLD`; **no AI efficacy or trading P&L was measured**.
- Raw result: [`results/shadow-openai.json`](../results/shadow-openai.json).
- Audit chain verification: valid.

## Automated checks

Eight `unittest` cases pass: credit market exclusion, capital/position/drawdown limits, spread and thin-exit-book filters, malformed AI response fail-closed, duplicate/chain audit behavior, next-bar opening fill, and disabled live command.
