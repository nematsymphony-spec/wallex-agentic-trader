# Starcoach × Wallex agentic trading challenge

Auditable Python prototype for the **BTCTMN, ETHTMN, SOLTMN, PUMPTMN** credit-account challenge. It collects completed hourly candles and current order books, obtains a structured OpenAI `BUY / SELL / HOLD` decision, applies deterministic risk limits, and records a hash-linked audit trail. The same `Engine.step` handles historical simulation and shadow mode.

**Status: no real exchange order can be sent from this revision.** Wallex's published [spot order endpoint](https://developers.wallex.ir/docs/spot-create-order) does not document a credit-subaccount routing parameter. The account's API management page also currently requires an advanced verification level before an API key can be created. `wallex-agent live` exits with code 3. These are release blockers, recorded in [implementation status](docs/IMPLEMENTATION_STATUS.md).

## Quick start

Python 3.9+; standard library only. From this directory:

```sh
PYTHONPATH=src python3 -m wallex_agent markets
PYTHONPATH=src python3 -m wallex_agent backtest \
  --start 2026-10-18T00:00:00Z --end 2026-10-21T00:00:00Z \
  --model rule --db results/new-backtest.db > results/new-backtest.json
PYTHONPATH=src python3 -m wallex_agent shadow --model rule --db results/new-shadow.db
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m wallex_agent verify-log results/new-backtest.db
PYTHONPATH=src python3 -m wallex_agent export-log results/new-backtest.db --last 20
```

For a genuine AI shadow cycle, set `OPENAI_API_KEY` in the process environment and run `shadow --model openai`. The default OpenAI model is `gpt-4.1-mini`; set `OPENAI_MODEL` if needed. Never commit keys or put them in command arguments. Without a key or with an invalid model response, the decision is `HOLD` and no order intent is produced. The API request uses strict [Responses structured output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), `store: false`, and records only the input hash, decision and rationale.

For an always-on Linux shadow process, adapt [`deploy/wallex-shadow.service`](deploy/wallex-shadow.service) to the server paths and use a root-owned environment file containing only the OpenAI key and model. `Restart=on-failure` retries after an API outage; an error cycle places no orders. The service has no exchange write capability.

## Decision path

1. Read Wallex's [active markets](https://developers.wallex.ir/docs/basic-services-markets), [package exclusions](https://developers.wallex.ir/docs/trade-credit-package-details), completed [historical candles](https://developers.wallex.ir/docs/generated/candle-history), and [order book](https://developers.wallex.ir/docs/basic-services-depth). Reject stale or invalid data.
2. Calculate three-hour and twelve-hour momentum, twelve-hour volatility, current spread, and top-five-level bid/ask depth. The AI may select or reject a trade and must name the signals used.
3. Reject markets outside the four-symbol candidate list or package permissions; check spread (35 bps max), five-times intended-order depth **on both sides of the book**, and capital limits. A buy targets 15% of equity, scales down when depth is thin, and is capped at 20% per position, with at most two open positions.
4. Pause buys after 2% daily drawdown (Tehran day) and after equity reaches 95% of the starting 5,000,000 TMN. Sells that reduce an existing position remain possible. A price gap can breach these thresholds before a response; the limits are controls, not guarantees.
5. Record snapshot → model decision → risk outcome → order intent → simulated fill as hash-linked SQLite events, with an idempotent client order ID. The log omits API secrets. Use `verify-log` to detect accidental edits or corruption.

## Evaluation

`backtest` uses completed one-hour bars for features and fills at the **next** bar's opening price with assumed bid/ask spread and 0.3% taker fee per side. Historical depth is unavailable, so the depth assumption is stated in each report. `--model rule` is only an engineering baseline and **does not demonstrate AI performance**. `--model openai` calls the same AI decider as shadow mode, but consumes API calls. Shadow mode uses live public market data and simulated fills; its portfolio is persisted between cycles in the SQLite state table. No shadow result should be presented as a real Wallex account result.

Sample reports: [four-market baseline](results/baseline-four-markets.json), [original three-market baseline](results/baseline-final.json), [shadow without an OpenAI key](results/shadow-openai.json), and [result notes](docs/RESULTS.md). [Market-selection notes](docs/MARKET_SELECTION.md) explain why PUMP/TMN is a candidate and why spot eligibility alone is insufficient.

## Runbook before a real release

See [implementation status](docs/IMPLEMENTATION_STATUS.md). A real-trading adapter must be added **only** after Wallex confirms a credit-only order route and a read-only account identity/balance check. Verify that the API key is scoped to the correct subaccount, and test order status, partial fills, cancellation, exchange min quantity, precision, stale-book handling, API interruption, and restart reconciliation. Use one process and one persistent SQLite database on an always-on server. Store secrets outside the repository. Freeze and hash the exact tested commit before submitting; deploy that same commit only.

The target code-freeze time in the team's plan is **1405/08/01 12:00 Asia/Tehran**. The public demo and GitHub URL remain to be produced after the real system is verified. [Demo outline](docs/DEMO.md) lists what to record without representing simulation as live trading.

## Sources

- [Challenge rules and deliverables](https://starcoach.ir/hackathon/agentic-ai/challenges/8)
- [Wallex package restrictions](https://developers.wallex.ir/docs/trade-credit-package-details)
- [Wallex spot order API](https://developers.wallex.ir/docs/spot-create-order)
- [Wallex main wallet separation](https://developers.wallex.ir/docs/basic-services-main-wallet)
- [OpenAI Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
