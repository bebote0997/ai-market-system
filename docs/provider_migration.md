# AI provider migration (prepared, not activated)

Prepared 2026-11-07. The running choice stays whatever `AI_FLOOR_AI_PROVIDER` already is (`deterministic` in code, or `openai` if that is set). Nothing in this change calls xAI, OpenAI, or X until a later approved deploy sets the mode and the key.

## What the floor uses today

| Role | Code | Config name | Default |
|---|---|---|---|
| Advisory AI | `ai/openai_provider.py` | `AI_FLOOR_AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` | code default is `deterministic` (no network) |
| Safe echo | `ai/provider.py` | `AI_FLOOR_AI_PROVIDER=deterministic` | yes |
| Prices | `data/twelve_data_provider.py` | `AI_FLOOR_MARKET_PROVIDER`, `TWELVE_DATA_API_KEY` | `twelve_data` |
| Alternate prices | `data/massive_provider.py` | `MASSIVE_API_KEY` | not the default |
| Macro / news | Finnhub, official hybrid, FXMacroData | `AI_FLOOR_MACRO_PROVIDER`, `FINNHUB_API_KEY`, `FXMACRODATA_API_KEY` | `none` |
| Practice orders | OANDA | `OANDA_PRACTICE_TOKEN`, `OANDA_ACCOUNT_ID` | routing off |
| Social / X | none | none | not used |

There is no X or Twitter client in this repository. Market prices and the broker book stay on Twelve Data and OANDA. Posts are not a price source. This chat having public X access does not mean the deployed service has an X token. No X adapter is added, because there is nothing to migrate and a social feed would not be allowed to replace those sources. Grok's X-search and web-search tools are not enabled on the trading call.

Exa is not a runtime dependency.

## xAI, when explicitly selected

`AI_FLOOR_AI_PROVIDER=xai` builds `ai.xai_provider.XAIProvider` and does not construct `OpenAIProvider`. If the call fails, times out, returns a tool call, or breaks the schema, the cycle does not trade. It does not retry on OpenAI.

Checked against the xAI docs on 2026-11-07:

| Name | Value |
|---|---|
| `XAI_API_KEY` | secret, not stored in the repo |
| `XAI_MODEL` | `grok-4.7` |
| `XAI_BASE_URL` | `https://api.x.ai/v1` (must be https) |
| Endpoint | `POST /v1/responses` |
| `XAI_TIMEOUT_SECONDS` | `30` |
| `XAI_RETRIES` | `2` (429 and 5xx; quota is not retried; Retry-After above 15 seconds stops) |
| `XAI_RATE_LIMIT_V2` | unset. Exact `1` replaces only the 429 path (below). Any other value keeps this row. |
| `XAI_MAX_INPUT_TOKENS` | `6000` (rough byte/4 budget, checked before the request) |
| `XAI_MAX_OUTPUT_TOKENS` | `800` |
| `XAI_MAX_TOTAL_TOKENS` | `8000` (a larger reported total fails closed) |

The request sets `store: false`, `reasoning.effort` to `low`, and `text.format` `json_schema` with `strict: true`. It does not send `tools`. Logs keep the kind, HTTP status, and token counts. They do not keep the key, the prompt, or the body. A test feeds a body that contains a fake key and checks that the exception text and `last_call` do not. `grok-4.7` can return encrypted reasoning; that content is not copied into the decision or the log.

## HTTP 429 retries (`XAI_RATE_LIMIT_V2`)

Checked in the code, not by replaying the Render logs. `ai/orchestrator.py` calls structure, liquidity, macro, the setup reviewer, and (when a plan exists) the trade reviewer one after another on the calling thread. `runtime/scheduler.py` does the same across symbols. There is no thread pool around xAI. A cycle is still a burst: up to five calls, and a macro `NO_DATA` retry runs the floor again. With the legacy loop, `XAI_RETRIES` defaults to 2. Without `Retry-After` the wait is `min(4, 0.4 * 2**attempt)` plus at most 0.1s of jitter. A `Retry-After` above 15 seconds does not wait; that call stops. Quota is not retried.

`XAI_RATE_LIMIT_V2` is optional and is not part of `RuntimeConfig.fingerprint`. Unset, empty, `0`, `true`, and ` 1` keep the legacy loop, including concurrent calls if some other path ever makes them. Exact `1`:

* One process-wide lock covers each xAI HTTP call and its 429 waits. Two calls do not overlap.
* HTTP 429 (not quota) waits `1, 2, 4, 8, 16` seconds plus jitter, at most six tries. Jitter is at most 1 second and never shortens a `Retry-After` value.
* A `Retry-After` header is the wait when it is present, including values above 15 seconds, up to 60 seconds. Above 60 seconds the call fails closed and does not sleep.
* All of those waits in the process share 90 seconds. After 120 seconds with no 429 wait, the budget resets, so the next 15-minute cycle starts clear. A wait that would pass the cycle deadline still stops, as it does today.
* The failure is still `RATE_LIMITED`. The adapter does not construct `OpenAIProvider`. The cycle's existing rule stands: an agent `ERROR` cannot become a paper order, and a deterministic `WATCH` stays `WATCH`.
* Each wait that actually happens logs `component=xai event=RATE_LIMIT_RETRY agent=… attempt=… wait_seconds=…`. The agent is a token or `-`. The line has no key, header, or body.

5xx, timeouts, and quota keep the legacy schedule even when the flag is on. Enabling it later is an owner action on the process environment. This change does not edit Render, `render.yaml`, or the scheduler. Rollback is unset or any value other than `1`.

Official pages checked 2026-11-07 (no live call, no account entitlement check):

* Model `grok-4.7`, structured outputs, reasoning efforts `low` / `medium` / `high` / `xhigh`, default `high`: https://docs.x.ai/developers/models/grok-4.7
* Responses API `reasoning.effort`, including `low`, and that it cannot be turned off: https://docs.x.ai/developers/model-capabilities/text/reasoning
* Responses example `text.format.type = json_schema` and `strict: true` on `grok-4.7`: https://docs.x.ai/developers/model-capabilities/text/structured-outputs
* Responses API is the current API; Chat Completions is legacy: https://docs.x.ai/developers/model-capabilities/text/best-practices
* `store: false` on `POST /v1/responses`: https://docs.x.ai/developers/model-capabilities/text/generate-text

The structured-outputs page also describes `response_format` for the older chat shape. This adapter uses the Responses `text.format` example, not Chat Completions. Whether this account can call `grok-4.7` was not tested. `python -m runtime.certify_providers --only xai` is the optional live check and was not run. The default `all` certification does not call xAI.

## Fail closed, fingerprint, and the 42-key set

A selected xAI call that is missing a key, over budget, timed out, rate-limited past the retry cap, quota-exhausted, incomplete, or off-schema raises and the cycle does not trade. The runtime does not construct `OpenAIProvider` in that mode. Tests cover those paths with a fake transport.

`AI_FLOOR_AI_PROVIDER` is an existing name, and it is already inside `RuntimeConfig.fingerprint`. Changing its value to `xai` changes the fingerprint, so `experiment_resumable` on the current demo database would not match. That is a coordinator-approved value change of an existing key, not something this PR does.

`XAI_API_KEY` is a **new name**. It is not in the current 42-key set (name hash `27efb336cec1008d`). Cloud preflight marks `xai` without that key as not ready, so the process would not start. The optional names `XAI_MODEL`, `XAI_BASE_URL`, `XAI_TIMEOUT_SECONDS`, `XAI_RETRIES`, `XAI_MAX_INPUT_TOKENS`, `XAI_MAX_OUTPUT_TOKENS`, and `XAI_MAX_TOTAL_TOKENS` also have code defaults, so they are not required for a later activation if those defaults are accepted. This PR does not add or edit any Render key. xAI cannot be turned on while the 42-key name set stays the same, because the key does not exist there. Proposed owner-approved delta, later and not now: add `XAI_API_KEY`, then set the existing `AI_FLOOR_AI_PROVIDER` to `xai`, and accept the fingerprint change on a flat account.

## Resuming the current experiment

The provider mode stays inside `RuntimeConfig.fingerprint`. Preflight does not drop that field, does not rewrite `run_metadata`, and does not create a new experiment. With no migration row, a different fingerprint still fails exactly as before. Startup does not write a row.

The only writer is the runner, and only after the owner queues a request from the dashboard. The dashboard already requires `AI_FLOOR_DASHBOARD_PASSWORD`. No new secret and no new env name are added for this step. The request is one flag in the broker-state DB, the same mailbox emergency-stop and manual close already use. It is not a command endpoint and it is not SQL.

On the Control page, an owner session can press **Registrar paso a xAI** and type `CAMBIAR A XAI`, or **Registrar vuelta a OpenAI** and type `VOLVER A OPENAI`. The dashboard queues the flag only when a read-only check sees no paper OPEN position, no paper order other than FILLED, CANCELLED, or REJECTED, and no broker-demo row in PENDING_SEND, OPEN, UNKNOWN, or PENDING_LIMIT. The runner, on its existing few-second poll and under its lock, reads the broker's open trades and pending orders fresh. A failed or missing read does not append and leaves the request queued. A drift block, an open trade, or a pending order refuses the request. The local paper book is checked as well. Only then does it append one row. The Control page shows whether that append is still pending, recorded, or refused. New pullback and EMA entries stay blocked until a run has been stored on the new fingerprint. The previous mode still resumes until that run; after it, only the recorded tail resumes. A later rollback is a new explicit row. The request names `openai` or `xai` only. The from/to payloads are built from the live config. A request cannot change any other fingerprint field.

The row stores the three zero counts, the freeze SHA, the starting equity, the actor, and the note. The table rejects UPDATE and DELETE. Repeating the same step does not add a second row. A step that does not continue the chain is rejected. Two writers take one write lock. A bad row does not fall back to the old resume rule.

Recording openai to xai while the process is still on openai does not break the next restart. Once a run is stored with the xAI fingerprint, only that tail resumes. Returning to OpenAI takes a new explicit row, and the same rule applies to it. `AI_FLOOR_GIT_COMMIT` must stay equal to the freeze SHA already stored in the database. No row has been written against the running database. The Control page shows the receipt (`pending`, `recorded`, or `refused`) before the provider value is changed.

## Order of activation

The deployed service is still the previous revision. A code deploy restarts the process once. That interruption is the usual one for this persistent disk. `autoDeploy` stays off. Nothing here restarts or edits Render.

1. Deploy this code with `AI_FLOOR_GIT_COMMIT` unchanged and `AI_FLOOR_AI_PROVIDER` still the current value. Leave `AI_FLOOR_EMA_CROSS` unset. `XAI_API_KEY` can be present; it does not switch the provider. There is no transition row yet, so resume uses the unchanged rule.
2. With the service up and the account flat, including pending orders, the owner confirms `CAMBIAR A XAI` on the Control page. The runner appends one openai to xai row. The live provider is still the old one. A restart here still resumes.
3. Set the existing `AI_FLOOR_AI_PROVIDER` to `xai` and restart. The freeze SHA stays the same. Resume uses the recorded chain. `XAI_API_KEY` must be set or preflight is not ready. This restart is not `python -m runtime.certify_providers --only xai`.

If step 3 happens before step 2, preflight is not ready and the service does not start, so the dashboard cannot record the row. Put `AI_FLOOR_AI_PROVIDER` back to the previous value and restart. Do not delete or replace the database.

## Rollback

While the service is running on xai, and the account is flat, the owner confirms `VOLVER A OPENAI`. The runner appends a second row. A restart before the env change still resumes, because xai is on the chain. Then set `AI_FLOOR_AI_PROVIDER` back to `openai` and restart. The freeze SHA stays the same. `ai/openai_provider.py` is unchanged. Unset `XAI_API_KEY` only if it should not remain available. Do not edit or delete the first row.
