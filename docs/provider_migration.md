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

The request sets `store: false`, `reasoning.effort: low`, and a strict JSON schema. It does not send `tools`. Logs keep the kind, HTTP status, and token counts. They do not keep the key, the prompt, or the body. `grok-4.7` can return encrypted reasoning; that content is not copied into the decision or the log.

`python -m runtime.certify_providers --only xai` is the optional live check. The default `all` certification does not call xAI.

Selecting `xai` changes `RuntimeConfig.fingerprint` because the provider mode is already part of it. On the current demo database that can fail `experiment_resumable`. Do not flip the mode on the running service until that is an approved deploy, with `XAI_API_KEY` present. Cloud preflight treats a selected xAI mode without that key as not ready, so the process does not start half-configured.

## Rollback

Set `AI_FLOOR_AI_PROVIDER` back to `openai` (or `deterministic`) and deploy that config. `ai/openai_provider.py` is unchanged. Unset `XAI_API_KEY` if it should not remain available. No order path changes in this PR.
