# Evaluation results (2026-10-22)

## Current full-universe engineering baseline

- Universe: all **62 currently active, package-183-allowed TMN spot markets**, after unioning Wallex's live exclusion response with the [357-market local exclusion snapshot](../config/package-183-exclusions.json). The 58 allowed USDT pairs are catalogued but are not funded by the TMN credit balance.
- Window: 2026-10-18 00:00 UTC through 2026-10-21 00:00 UTC, completed hourly bars. One global decision per eligible hour, with six 12-hour volatility windows and 50/30/20 daily weights.
- Model: deterministic global rule baseline, **not AI**. 66 decision cycles and 38 simulated fills; six additional cycles held because a position's fresh bar was missing. Starting equity 5,000,000 TMN; ending equity **4,925,240.38 TMN**; net return **−1.4952%**; maximum marked drawdown **3.5426%**.
- Costs and fills: assumed 20 bps bid/ask spread and 0.3% taker fee on each side, synthetic next-hour-open fills and constant sufficient depth. Historical order books were unavailable. Current market eligibility applied to past data introduces survivorship bias. The simulation does not prove profit or executable exchange fills.
- Reproduce: [`results/full-universe-baseline-current.json`](../results/full-universe-baseline-current.json). Its local SQLite event log verifies with `wallex-agent verify-log`; the SQLite file is excluded from Git.

## Current public-data scan and shadow smoke test

- Scan: [raw result](../results/full-universe-scan-current.json). 62 TMN markets allowed; 30 had 73 consecutive completed hourly candles and readable books; 32 were rejected for insufficient continuous candles. Of the 30, 15 passed all admission gates and 15 failed the 35 bps spread limit at that instant. These are time-sensitive observations.
- Shadow: [raw result](../results/full-universe-shadow-current.json). A second scan had 16 eligible markets. `OPENAI_API_KEY` was absent, so the global AI decider returned `HOLD`. The shadow audit chain verified; **no model call, AI effectiveness, real order, or trading P&L was measured**.

## Earlier prototypes, kept for context

The original [three-market baseline](../results/baseline-final.json) returned −0.9118%; a later [four-market baseline](../results/baseline-four-markets.json) returned +0.2652% on the same nominal window and synthetic costs. They used a different per-market decision flow and are not directly comparable with the present global selector. The broader current baseline is negative, so expanding the universe alone has not demonstrated an edge.

## Automated checks

Fourteen `unittest` cases pass, covering the completed six-window calculation, dynamic exclusion floor, bounded volatility score, valid and invalid global AI selections, market/risk gates, next-open simulation, audit integrity and disabled live execution.
