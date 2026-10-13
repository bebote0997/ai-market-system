# AI Runtime Specification — Fase 6A

## Regla central

```
AI INTERPRETS.
PYTHON VALIDATES.
RISK ENGINE AUTHORIZES.
PAPER BROKER EXECUTES.
```

Ningún proveedor IA puede: inventar precios, modificar OHLC, modificar equity, modificar la
cantidad aprobada, saltarse el Risk Engine, aprobar más de 1% de riesgo, bajar el R:R mínimo,
ejecutar órdenes, modificar `PaperAccount`/`PaperPosition`, ni acceder a un broker real.
`REAL_EXECUTION` permanece `DISABLED`.

## Flujo

```
deterministic Structure   -> Structure AI
deterministic Liquidity   -> Liquidity AI
deterministic Macro/News  -> Macro AI
                           -> AI Setup Reviewer   (lee SetupAssessment, no lo cambia)
deterministic Setup Validator (ya calculado)
deterministic Trade Planner   (ya calculado)
                           -> AI Trade Reviewer    (lee TradePlan, no lo cambia)
Deterministic Risk Engine (ya calculado, autoridad final)
```

`ai/orchestrator.py` es una capa de **composición** sobre un `FloorRunReport` ya producido por
`floor/orchestrator.py`. No reemplaza ni reejecuta el orquestador determinista existente.

## Contratos (`ai/contracts.py`)

### `AIRequest` (frozen)

`schema_version`, `run_id`, `as_of`, `symbol`, `agent_name`, `role`, `market_context`,
`deterministic_evidence` (tupla de dicts, cada uno con `evidence_id`), `allowed_actions`,
`constraints`, `prompt_version`, `deterministic_preferred`.

### `AIResponse` (frozen)

`schema_version`, `run_id`, `as_of`, `symbol`, `agent_name`, `status`
(`OK`/`PARTIAL`/`NO_DATA`/`ERROR`), `bias` (`BULLISH`/`BEARISH`/`NEUTRAL`/`UNKNOWN`),
`confidence` (0.0–1.0), `recommendation` (opcional: `AGREE`/`CAUTION`/`DISAGREE`/
`INSUFFICIENT_DATA`/`ACCEPT`/`REJECT_RECOMMENDATION`), `observations`,
`supporting_evidence`, `conflicting_evidence`, `risks`, `invalidation_conditions`,
`warnings`, `model_metadata`, `reasoning_summary` (texto libre solo para UI/auditoría, nunca
para decisiones entre componentes).

### `AIFloorReport` (frozen)

`run_id`, `as_of`, `symbol`, `deterministic_reports`, `ai_structure`, `ai_liquidity`,
`ai_macro`, `ai_setup_review`, `trade_plan`, `ai_trade_review`, `risk_decision`,
`final_status` (`NO_DATA`/`NO_SETUP`/`WATCH`/`AI_CAUTION`/`RISK_REJECTED`/`PLAN_READY`/
`ERROR`), `warnings`, `prompt_versions`, `provider_metadata`.

## Source grounding

`validate_ai_response(response, request)` rechaza una respuesta si:

- `schema_version`, `run_id`, `symbol`, `agent_name` o `as_of` no coinciden exactamente con el
  `AIRequest`.
- `status`, `bias` o `recommendation` no están en el conjunto permitido.
- `confidence` no es un número finito (no bool) entre 0.0 y 1.0.
- `supporting_evidence` o `conflicting_evidence` citan un `evidence_id` que no existe en
  `request.deterministic_evidence`.

Una respuesta rechazada nunca llega a otro componente: `ai/runtime.call_agent` la sustituye por
un `AIResponse(status="ERROR")` local.

## Provider abstraction (`ai/provider.py`)

- `AIProvider` (ABC): `generate(request) -> AIResponse`.
- `DeterministicAIProvider`: proveedor por defecto sin red; solo resume/echoa evidencia ya
  suministrada, nunca inventa un dato nuevo.
- `FakeAIProvider`: doble de prueba (respuesta fija, función de respuesta o excepción
  configurable) para ejercitar aislamiento de fallos y casos adversariales.

Un proveedor LLM real se conecta implementando la misma interfaz; ni el Risk Engine, ni el
Paper Broker, ni los scouts deterministas necesitan cambiar.

## Failure isolation y control de costes (`ai/runtime.py`)

- Si no hay evidencia determinista utilizable, no se llama al proveedor: se retorna `NO_DATA`
  directamente (sin incrementar las llamadas del proveedor).
- Si el proveedor lanza una excepción, se captura y se convierte en `ERROR` local; el resto del
  run determinista continúa.
- Ningún resultado IA, ni siquiera `OK`/`AGREE`, otorga permiso de ejecución por sí mismo.

## Audit log

`AuditLog` (en memoria) registra por llamada: `run_id`, agente, versión de prompt, IDs de
evidencia, respuesta, resultado de validación y metadata del proveedor. Nunca registra API
keys, tokens ni credenciales. Persistencia durable queda para Fase 6C.

## Prompts versionados (`ai/prompts.py`)

`STRUCTURE_PROMPT_VERSION`, `LIQUIDITY_PROMPT_VERSION`, `MACRO_PROMPT_VERSION`,
`SETUP_REVIEW_PROMPT_VERSION`, `TRADE_REVIEW_PROMPT_VERSION` (todas `"1.0"`). Cada prompt
documenta ROLE, INPUT CONTRACT, ALLOWED INTERPRETATION, FORBIDDEN ACTIONS, OUTPUT SCHEMA y
comportamiento `NO_DATA`.

## Inmutabilidad

`MarketBar`, `TradePlan`, `RiskDecision` e `InstrumentSpec` son dataclasses `frozen=True`:
cualquier intento de escritura lanza `dataclasses.FrozenInstanceError`. `PaperAccount` nunca se
entrega directamente a la capa IA; `ai.runtime.snapshot_account` retorna una copia profunda de
solo lectura.

## Autoridad final

`ai/orchestrator.py` nunca convierte `NO_SETUP` o `WATCH` deterministas en un estado
ejecutable, ni convierte un `RiskDecision` `REJECTED` en `APPROVED`. Cuando el determinista
llega a `PLAN_READY` pero el AI Setup Reviewer responde `DISAGREE` o el AI Trade Reviewer
responde `REJECT_RECOMMENDATION`, el `final_status` del `AIFloorReport` se marca `AI_CAUTION`
como señal advisory, sin modificar el `RiskDecision` subyacente.

## No implementado en Fase 6A

Proveedor LLM real, API keys, scheduler, base de datos, Redis, Docker, broker real, órdenes
reales, dinero real, UI completa de Fase 6B.
