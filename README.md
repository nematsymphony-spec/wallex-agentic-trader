# Starcoach × Wallex agentic trading challenge

Auditable Python prototype for the Wallex credit-account challenge. It discovers **every active, package-allowed TMN spot market at runtime**, collects completed hourly candles and current order books, asks OpenAI to choose one market or `HOLD` from all eligible markets in one structured decision, applies deterministic risk limits, and records a hash-linked audit trail. The same `Engine.cycle` and `Engine.step` handle historical simulation and shadow mode. USDT markets are catalogued but excluded from trading decisions while the credit balance is TMN.

**Status: no real exchange order can be sent from this revision.** Wallex's published [spot order endpoint](https://developers.wallex.ir/docs/spot-create-order) does not document a credit-subaccount routing parameter. The account's API management page also currently requires an advanced verification level before an API key can be created. `wallex-agent live` exits with code 3. These are release blockers, recorded in [implementation status](docs/IMPLEMENTATION_STATUS.md).

## Quick start

Python 3.9+; standard library only. From this directory:

```sh
PYTHONPATH=src python3 -m wallex_agent markets
PYTHONPATH=src python3 -m wallex_agent scan --top 15
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

1. Read Wallex's [active markets](https://developers.wallex.ir/docs/basic-services-markets), [package exclusions](https://developers.wallex.ir/docs/trade-credit-package-details), completed [historical candles](https://developers.wallex.ir/docs/generated/candle-history), and [order book](https://developers.wallex.ir/docs/basic-services-depth). Union the live exclusion list with the [357-market package-183 safety snapshot](config/package-183-exclusions.json) corresponding to the user's forbidden-market list. Discover the full allowed TMN universe each cycle; reject stale or invalid data.
2. From 73 consecutive completed hourly closes, calculate six non-overlapping 12-hour realized-volatility windows as the square root of summed squared hourly percentage returns. Average each adjacent pair into a day, then weight the newest, middle, and oldest days 50%, 30%, and 20%. Also calculate three-hour and twelve-hour momentum, twelve-hour volatility, trailing 24-hour quote volume, current spread, and top-five bid/ask quote depth.
3. Admit a market only if its data is fresh, spread is at most 35 bps, **each side** of the book has at least five times the minimum 100,000 TMN order, and 24-hour quote volume is at least 50 million TMN. Rank admitted markets with a bounded volatility percentile bonus (25%), trend (35%), volume (20%), and spread cost (20%). This is a priority score, not a return forecast. Send **all** eligible markets and held positions together to one AI call; it may choose one `BUY`, one `SELL`, or `HOLD` and must name its signals.
4. Apply an independent risk veto. A buy targets 15% of equity, scales down when depth is thin, and is capped at 20% per position, with at most two open positions.
5. Pause buys after 2% daily drawdown (Tehran day) and after equity reaches 95% of the starting 5,000,000 TMN. Sells that reduce an existing position remain possible. A price gap can breach these thresholds before a response; the limits are controls, not guarantees.
6. Record full-universe scan → model decision → risk outcome → order intent → simulated fill as hash-linked SQLite events, with an idempotent client order ID. The log omits API secrets. Use `verify-log` to detect accidental edits or corruption.

## Evaluation

`backtest` uses completed one-hour bars for features and fills at the **next** bar's opening price with assumed bid/ask spread and 0.3% taker fee per side. Historical depth is unavailable, so the depth assumption is stated in each report. `--model rule` is only an engineering baseline and **does not demonstrate AI performance**. `--model openai` calls the same AI decider as shadow mode, but consumes API calls. Shadow mode uses live public market data and simulated fills; its portfolio is persisted between cycles in the SQLite state table. No shadow result should be presented as a real Wallex account result.

Current reports: [full-universe baseline](results/full-universe-baseline-current.json), [full-universe scan](results/full-universe-scan-current.json), [shadow without an OpenAI key](results/full-universe-shadow-current.json), and [result notes](docs/RESULTS.md). [Market-selection notes](docs/MARKET_SELECTION.md) explain the dynamic universe and why high volatility alone is insufficient. Earlier three- and four-market reports used a different per-market decision flow and are retained only as historical prototypes.

## Runbook before a real release

See [implementation status](docs/IMPLEMENTATION_STATUS.md). A real-trading adapter must be added **only** after Wallex confirms a credit-only order route and a read-only account identity/balance check. Verify that the API key is scoped to the correct subaccount, and test order status, partial fills, cancellation, exchange min quantity, precision, stale-book handling, API interruption, and restart reconciliation. Use one process and one persistent SQLite database on an always-on server. Store secrets outside the repository. Freeze and hash the exact tested commit before submitting; deploy that same commit only.

The target code-freeze time in the team's plan is **1405/08/01 12:00 Asia/Tehran**. The public demo and GitHub URL remain to be produced after the real system is verified. [Demo outline](docs/DEMO.md) lists what to record without representing simulation as live trading.

## Sources

- [Challenge rules and deliverables](https://starcoach.ir/hackathon/agentic-ai/challenges/8)
- [Wallex package restrictions](https://developers.wallex.ir/docs/trade-credit-package-details)
- [Wallex spot order API](https://developers.wallex.ir/docs/spot-create-order)
- [Wallex main wallet separation](https://developers.wallex.ir/docs/basic-services-main-wallet)
- [OpenAI Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
