# Implementation and release status

| Item | Status | Evidence / next requirement |
|---|---|---|
| Public market discovery and package exclusion | Done for public data | `markets` reads Wallex endpoints at runtime and unions package 183 exclusions with a fixed 357-market safety snapshot. The observed allowlist had 62 TMN and 58 USDT pairs. |
| Market data and features | Done | Completed hourly OHLCV, six 12-hour volatility windows with 50/30/20 day weights, and top-five order-book levels. Missing continuous bars are excluded. |
| Global structured OpenAI decision | Implemented, unverified with real model | Strict JSON schema, one decision over all eligible markets and holdings, fail-closed `HOLD`; no OpenAI API key in this environment. |
| Risk limits | Implemented for simulation/shadow | 20% per position, two positions, 2% daily buy pause, 5% starting-equity buy pause, spread/depth gates. |
| Backtest and shadow | Implemented | Full-universe rule baseline returned −1.4952% under stated synthetic costs. A public-data AI shadow smoke test returned `HOLD` without a key. Longer real AI shadow evaluation is still required. |
| Credit account authentication | **Blocked** | User's Wallex API management page requires advanced verification for key creation. No key was created or collected. |
| Credit-only real order routing | **Blocked** | Published `POST /v1/account/orders` has no documented credit-subaccount selector. It may route to the main account. `live` is deliberately disabled. Require Wallex confirmation and read-only account verification before implementing writes. |
| Exchange order lifecycle | Not implemented | After credit route is established: test server-side idempotency, partial fills, status polling, cancellation, precision, restart reconciliation. |
| GitHub repository and five-minute public demo | Not published | Publish only the tested version; video must show actual verified behavior. |

No order was sent to Wallex, and no wallet linking or manual account transaction was performed. Do not run a generic spot order API key against the main account as a shortcut.
