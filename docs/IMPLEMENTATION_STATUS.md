# Implementation and release status

| Item | Status | Evidence / next requirement |
|---|---|---|
| Public market discovery and package exclusion | Done | `markets` reads Wallex endpoints at runtime; three target markets allowed in the observed response. |
| Market data and features | Done | Completed hourly OHLCV and top-five order-book levels, conservative validation. |
| Structured OpenAI decision | Implemented, unverified live | Strict JSON schema and fail-closed `HOLD`; no OpenAI API key in this environment. |
| Risk limits | Implemented for simulation/shadow | 20% per position, two positions, 2% daily buy pause, 5% starting-equity buy pause, spread/depth gates. |
| Backtest and shadow | Implemented | Historical engineering baseline and one public-data shadow smoke test recorded. Longer AI shadow test is still required. |
| Credit account authentication | **Blocked** | User's Wallex API management page requires advanced verification for key creation. No key was created or collected. |
| Credit-only real order routing | **Blocked** | Published `POST /v1/account/orders` has no documented credit-subaccount selector. It may route to the main account. `live` is deliberately disabled. Require Wallex confirmation and read-only account verification before implementing writes. |
| Exchange order lifecycle | Not implemented | After credit route is established: test server-side idempotency, partial fills, status polling, cancellation, precision, restart reconciliation. |
| GitHub repository and five-minute public demo | Not published | Publish only the tested version; video must show actual verified behavior. |

No order was sent to Wallex, and no wallet linking or manual account transaction was performed. Do not run a generic spot order API key against the main account as a shortcut.
