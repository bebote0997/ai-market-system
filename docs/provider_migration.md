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
| `XAI_MAX_INPUT_TOKENS` | `6000` (rough byte/4 budget, checked before the request) |
| `XAI_MAX_OUTPUT_TOKENS` | `800` |
| `XAI_MAX_TOTAL_TOKENS` | `8000` (a larger reported total fails closed) |

The request sets `store: false`, `reasoning.effort` to `low`, and `text.format` `json_schema` with `strict: true`. It does not send `tools`. Logs keep the kind, HTTP status, and token counts. They do not keep the key, the prompt, or the body. A test feeds a body that contains a fake key and checks that the exception text and `last_call` do not. `grok-4.7` can return encrypted reasoning; that content is not copied into the decision or the log.

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

The provider mode stays inside `RuntimeConfig.fingerprint`. Preflight does not drop that field, does not rewrite `run_metadata`, and does not turn `experiment_resumable` off as a policy. With no migration row, a different fingerprint still fails exactly as before.

`runtime/provider_transition.py` can append one immutable row only when an operator supplies the from and to payloads, a note, and `open_positions == 0`. The only allowed difference is `ai_provider_mode`, and only among `deterministic`, `openai`, and `xai`. Startup and preflight never write that row. A bad row, a second field, a fingerprint that is not on the recorded chain, or a missing pre-transition fingerprint does not resume. Reversal is a second explicit row back to the previous payload, not an edit of the first. No such row has been written. This is not approval to migrate the running demo.

If that record is never written, the compatible alternative is a new experiment: keep the current database as history, start the next one only when the owner says so, and do not delete the old account. That start is not performed here.

## Rollback

Set `AI_FLOOR_AI_PROVIDER` back to `openai` (or `deterministic`) and deploy that config. `ai/openai_provider.py` is unchanged. Unset `XAI_API_KEY` if it should not remain available. No order path changes in this PR.
