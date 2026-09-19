# Agent Changelog

## 2026-09-19 — Freeze oficial del experimento PAPER

- `status`: EXPERIMENT_FROZEN; baseline f5032baeb87766ad74905093c1b4195117995092.
- `economics`: USD, equity 10000, comisión/spread adicional/swap 0; fill al Open
  siguiente barra elegible y gaps actuales, aprobados expresamente por el usuario.
- `policy`: Solo fixes técnicos que invaliden experimento/integridad; cambios con
  impacto económico o decisional requieren evaluar nueva baseline y reinicio.
- `validation`: 507 tests OK; diff check y secret scan PASS. Este commit es
  exclusivamente documental y no cambia el ejecutable probado.
- `activation`: Runner/scheduler apagados; experimento no iniciado; Render sin
  desplegar la nueva baseline. Ver EXPERIMENT_FREEZE.md para pasos pendientes.

## 2026-09-19 — Auditoría final y preparación de freeze PAPER

- `agent`: Codex.
- `base`: f9ae6ce79deb9baaa0dab0faa0271e462599d843.
- `bugs`: Frescura/sesión solo por slot, ausencia de contratos en cloud,
  recuperación incompleta tras reinicio rápido, awareness FXMacroData omitido,
  y SESSION_SKIPPED contado por DAILY_SUMMARY. Reproducidos con fixtures offline.
- `fixes`: Doble gate temporal con reloj real; recuperación reciente solo bajo
  lock cloud exclusivo; awareness y conteo corregidos. Contratos PAPER aprobados
  con multiplier=1 y quantity grid inferior, sin modificar Risk Engine ni precios.
- `validation`: 489 tests baseline; 507 tests finales OK (18 nuevos);
  ver AUDIT_FREEZE.md y PAPER_EXECUTION_SPEC.md.
- `economics`: Usuario confirmó USD, equity 10000, comisión/spread adicional/swap
  0, fill al Open de siguiente barra elegible y gaps SL/TP existentes.
- `state`: Preparado para freeze oficial de código; sin activar runner/scheduler/
  experimento. Render sigue desplegado en f3af0c3; no se despliega en esta misión.

## 2026-09-19 — DAILY_SUMMARY y validación de notificaciones

- `agent`: Codex.
- `task`: Resumen del día UTC anterior completo desde SQLite: ciclos/símbolo,
  setups/riesgo, órdenes/posiciones PAPER, PnL/equity, problemas de proveedor,
  macro HIGH y estado operativo persistido. Contrato y ejemplo en DAILY_SUMMARY.md.
- `delivery`: Snapshot confirmado antes de Slack; deduplicación por día durable,
  incluso concurrente y tras reinicio; transporte fallido aislado del trading.
- `scope`: Solo supervisión y tests; sin cambios en estrategia, agentes/prompts,
  riesgo, gates, providers, sesiones ni ejecución PAPER. Sin activar experimento.
- `validation`: Baseline aislada 480 tests; final 489 tests OK, con 9 nuevos
  tests offline; Slack falso; git diff --check y secret scan por patrones.
- `workspace`: Cambios previos no committed de proveedores excluidos del commit.

## 2026-09-19 — Cloud preflight para FXMacroData certificado

- `agent`: Codex.
- `task`: Reconocer `fxmacrodata` como proveedor macro certificado cuando su clave existe, manteniendo `none` solo para infraestructura sin readiness de experimento.
- `safety`: PAPER only; runner/scheduler y experimento siguen apagados; ningún otro gate se relajó.
- `tests`: añadidos casos de FXMacroData con y sin credencial y preservado el caso `none`.

## 2026-09-19 — FXMacroData macro provider

- `agent`: Codex.
- `task`: Integrar y certificar calendario USD/EUR, announcements, predictions, changes y research/panel con caché, errores explícitos y no-lookahead por fetch/publicación/vintage.
- `decision`: `FXMACRODATA_CERTIFIED` live; adapter configurable y todavía no activo. El Blueprint conserva macro `none`.
- `safety`: runner/scheduler `0`, PAPER only; sin experimento, commit ni push.
- `tests_after`: 485 OK; certificación live PASS; `git diff --check` y secret scan OK.

## 2026-09-18 — Infraestructura cloud con macro diferido

- `agent`: Codex.
- `task`: Separar readiness de infraestructura y experimento, fijar macro efectivo `none`/`NO_DATA`, reforzar el gate del supervisor/runner, conservar una sola autoridad PAPER, redacción y límite de exportaciones, y retirar Finnhub de las variables del Blueprint activo.
- `decision`: INFRA_READY para despliegue controlado con montaje/entorno simulados; EXPERIMENT_READY=false por MACRO_PROVIDER_NOT_CERTIFIED. EODHD Free HTTP 403, sin adapter nuevo ni más consultas macro.
- `safety`: scheduler y runner apagados, real execution deshabilitada, sin despliegue, experimento, commit ni push.
- `tests_after`: 470 OK; `git diff --check` y secret scan OK; preflight simulado INFRA_READY/experiment_ready=false. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-18 — Evaluación del híbrido macro oficial

- `agent`: Codex.
- `task`: Implementar `OfficialMacroProvider` sobre calendarios ICS BLS/BEA/Eurostat y RSS Fed/BCE, con procedencia, precisión temporal, caché, aislamiento de fallos y evidencia en `ReviewReport`.
- `decision`: OFFICIAL_MACRO_INSUFFICIENT. BEA, Eurostat, Fed y BCE accesibles; BLS Access Denied. Los RSS monetarios no ofrecen agenda futura FOMC/BCE; Eurostat observado publica solo fecha. USD y EUR siguen PARTIAL.
- `safety`: Finnhub y FRED inactivos; macro `none` en Blueprint, scheduler apagado; sin despliegue, experimento, commit ni push.
- `tests_after`: 467 OK; `git diff --check` y secret scan OK; preflight NOT_READY por gate macro y entorno local. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-17 — Preparación cloud posterior a Fase 7

- `agent`: Codex.
- `task`: Preparar Render, persistencia durable, dashboard privado, Slack, evidencias compartidas y Finnhub Economic Calendar sin activar el experimento.
- `tests_before`: 447 OK.
- `tests_after`: 455 OK offline.
- `status`: READY_FOR_CLOUD_SETUP; Finnhub live sin certificar por HTTP 403 con la clave presente.
- `safety`: PAPER, scheduler y runner apagados; sin despliegue, mensajes Slack reales, commit ni push.

## 2026-09-17 — Evaluación FRED/ALFRED

- `agent`: Codex.
- `task`: Evaluar oficialmente FRED/ALFRED como fuente macro única para XAUUSD/EURUSD y endurecer el gate temporal.
- `decision`: FRED_INSUFFICIENT; calendario futuro solo por fecha, sin hora/zona, consenso ni importancia; FOMC/ECB no acreditados como cobertura completa. No se creó adapter activo ni se solicitó clave.
- `status`: Finnhub SUPPORTED/NOT_ACTIVE, macro provider `none`, sin despliegue, scheduler, experimento, commit ni push.
- `tests_after`: 459 OK; `git diff --check` y secret scan OK.

## 2026-09-17 — Fase 6D: Twelve Data activo

- `agent`: Codex
- `task`: Añadir Twelve Data como MarketDataProvider activo para la demo PAPER, con XAU/USD y EUR/USD en 5m/15m/1h; conservar Massive configurable/NOT_ACTIVE y NAS100 soportado/NOT_ENABLED.
- `tests_before`: 427 OK.
- `tests_after`: 435 OK en la suite final de cierre.
- `safety`: barras cerradas UTC, ticker exacto, validación OHLC, caché únicamente de datos actuales, freshness gate intacto, 429/5xx con reintentos acotados y sin exposición de credenciales. Scheduler y experimento de 14 días siguen apagados.
- `certification`: PASS de OpenAI `gpt-5.6-terra` y Twelve Data XAUUSD/EURUSD en 5m/15m/1h, 17 de septiembre de 2026. Ver `PROVIDER_CERTIFICATION.md` y `AUDIT_PHASE6D.md`.

## 2026-09-16 — Fase 6C

- `agent`: Codex
- `task`: Implementar runtime PAPER durable con SQLite, scheduler, recovery, health y compuerta final de ejecución.
- `tests_before`: 389 OK.
- `tests_after`: 405 OK.
- `files_created`: `storage/`, `runtime/`, `test_runtime.py`, `OPERATIONAL_RUNTIME_SPEC.md`, `AUDIT_PHASE6C.md`.
- `files_modified`: `.gitignore`, `ui/adapters.py`, `ui/state.py`, `ui/app.py`, `ui/pages/journal.py`, `ui/pages/system.py`, `ARCHITECTURE.md`, `ROADMAP.md`, `UI_SPEC.md`, `CHANGELOG_AGENT.md`.
- `safety`: PAPER only; `AI_CAUTION` bloquea progresión incluso con riesgo APPROVED; datos stale, barras abiertas y fallos bloquean nuevos paper orders.
- `next_phase`: preparación Fase 7, no implementada.

## 2026-09-16 — Fase 6B

- `agent`: Codex
- `task`: Implementar Hybrid Trading Floor UI en Streamlit y Plotly, preservando `app.py` legacy.
- `tests_before`: 381 OK.
- `tests_after`: 389 OK.
- `files_created`: `ui/`, `test_ui.py`, `requirements-ui.txt`, `UI_SPEC.md`, `AUDIT_PHASE6B.md`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `integration`: `FloorRunReport → AIFloorReport → FloorViewModel`; renderers de solo lectura.
- `safety`: PAPER MODE, REAL EXECUTION DISABLED, RiskDecision como única autoridad, SAMPLE / DEMO aislado.
- `next_phase`: Fase 6C, no implementada.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 6A — AI Agent Runtime
- `task`: Construir el motor de agentes IA advisory sobre el Trading Floor determinista existente, sin reemplazarlo.
- `tests_before`: 315 tests OK.
- `tests_after`: 381 tests OK.
- `files_created`: `ai/__init__.py`, `ai/contracts.py`, `ai/provider.py`, `ai/runtime.py`, `ai/prompts.py`, `ai/orchestrator.py`, `ai/agents/__init__.py`, `ai/agents/structure_ai.py`, `ai/agents/liquidity_ai.py`, `ai/agents/macro_ai.py`, `ai/agents/setup_reviewer_ai.py`, `ai/agents/trade_reviewer_ai.py`, `test_ai_contracts.py`, `test_ai_provider.py`, `test_ai_runtime.py`, `test_ai_agents.py`, `test_ai_reviewers.py`, `test_ai_orchestrator.py`, `AI_RUNTIME_SPEC.md`, `AUDIT_PHASE6A.md`.
- `files_modified`: `ARCHITECTURE.md`, `TRADING_FLOOR_SPEC.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_abstraction`: `AIProvider.generate(request) -> AIResponse`; `DeterministicAIProvider` es el proveedor por defecto (sin red, sin API keys) y `FakeAIProvider` es el doble de prueba; un proveedor LLM real puede añadirse después sin tocar Risk Engine, Paper Broker ni scouts.
- `grounding_policy`: `validate_ai_response` rechaza cualquier respuesta con `run_id`, `symbol`, `agent_name`, `as_of`, `bias`, `recommendation` o `confidence` fuera de lo esperado, o que cite un `evidence_id` no presente en el `AIRequest`.
- `failure_isolation`: una excepción del proveedor se captura y se convierte en `AIResponse(status="ERROR")` local; el resto del run determinista continúa.
- `cost_control`: si no hay evidencia determinista utilizable, no se invoca al proveedor y se retorna `NO_DATA` directamente.
- `authority_policy`: `ai/orchestrator.py` compone sobre un `FloorRunReport` ya calculado por `floor/orchestrator.py` (no lo modifica); `NO_SETUP`, `WATCH` y `RISK_REJECTED` deterministas nunca se convierten en un estado ejecutable; un `PLAN_READY` con revisión IA adversa (`DISAGREE`/`REJECT_RECOMMENDATION`) se reporta como `AI_CAUTION` sin alterar el `RiskDecision` original.
- `immutability`: `MarketBar`, `TradePlan`, `RiskDecision` e `InstrumentSpec` son dataclasses frozen (cualquier intento de escritura lanza `FrozenInstanceError`); `PaperAccount` solo se expone a la capa IA mediante `ai.runtime.snapshot_account`, una copia profunda de solo lectura.
- `audit_log`: estructura en memoria con `run_id`, agente, versión de prompt, IDs de evidencia, respuesta, resultado de validación y metadata del proveedor; sin API keys ni credenciales. Persistencia durable diferida a Fase 6C.
- `real_execution_check`: REAL_EXECUTION = DISABLED; `ai/` no importa `execution.paper_broker` ni `execution.trade_manager`.
- `warnings`: warnings legacy de Streamlit/pandas preexistentes; sin red, broker ni dinero real.
- `known_debt`: sin proveedor LLM real conectado (deliberado), sin scheduler, sin persistencia durable del audit log.
- `next_phase`: Fase 6B — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Crear protocolo persistente y documentación base para AI Trading Floor V2.
- `files_changed`: `AGENTS.md`, `TRADING_FLOOR_SPEC.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: No se ejecutaron cambios de código; baseline confirmado con 230 tests OK.
- `decisions`: Fase 0 marcada como completada; Fase 1 marcada como siguiente. Se conserva el prototipo legacy y se separan contratos, datos, agentes y motores deterministas en el diseño futuro.
- `warnings`: `git` no estaba disponible en el PATH de la terminal; no se modificó código Python.
- `next_step`: Diseñar e implementar Fase 1 solo con autorización explícita.

## Estado inicial conocido

- Baseline actual: `230 tests OK`.
- Checkpoint previo: Risk Engine V1 existente.
- No se ha autorizado todavía la implementación de Fase 1.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Implementar el vertical slice determinista de Fase 1: contratos, InstrumentSpec, timeframes y Risk Engine LONG/SHORT con simulación explícita.
- `files_changed`: `core/contracts.py`, `core/timeframes.py`, `core/__init__.py`, `data/instruments.py`, `data/__init__.py`, `riesgo.py`, `simulador.py`, `estrategia.py`, `metricas_estrategia.py`, `evaluacion_estrategia.py`, `test_core_contracts.py`, `test_risk_v2.py`, `test_simulador.py`, `test_metricas_estrategia.py`, `test_evaluacion_estrategia.py`, `ROADMAP.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: 237 tests OK.
- `decisions`: Mantener las APIs legacy; añadir una ruta V2 explícita. Rechazar instrumentos sin multiplicador contractual conocido. Mantener UTC y timeframes 1h/15m/5m. Usar APPROVED/REJECTED determinista y soportar LONG/SHORT.
- `warnings`: Warnings existentes de Streamlit y pandas durante la suite; no se conectó red ni broker.
- `next_step`: Fase 2: Structure Agent y Liquidity Agent deterministas.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2
- `task`: Implementar Structure Agent y Liquidity Agent deterministas con contrato AgentMessage versionado.
- `tests_before`: 237 tests OK.
- `tests_after`: 248 tests OK.
- `files_created`: `agents/__init__.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `test_structure_agent.py`, `test_liquidity_agent.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `definitions_used`: swings confirmados con una barra cerrada posterior; HH/HL/LH/LL por comparación de swings; soporte/resistencia como extremos de swings; displacement como cuerpo/rango >= 0.6; equal levels con tolerancia relativa 0.1%; sweep high/low por ruptura del extremo previo y cierre de vuelta dentro.
- `heuristics`: displacement y equal-level detection están marcados `HEURISTIC`; order blocks no se implementaron.
- `warnings`: warnings preexistentes de Streamlit y pandas durante tests; sin red ni broker.
- `next_phase`: Fase 3 — Macro/News Agent.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4
- `task`: Implementar Setup Validator, Trade Planner y Orchestrator deterministas.
- `tests_before`: 266 tests OK.
- `tests_after`: 278 tests OK.
- `files_created`: `agents/setup_validator.py`, `agents/trade_planner.py`, `floor/__init__.py`, `floor/orchestrator.py`, `test_setup_validator.py`, `test_trade_planner.py`, `test_orchestrator.py`.
- `files_modified`: `core/contracts.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`, `AUDIT_PRE_PHASE4.md`.
- `setup_policy`: requiere los tres timeframes explícitos; estructura compatible; evidencia 15m y liquidez 5m; macro HIGH ACTIVE_WINDOW bloquea; estados NO_SETUP/WATCH/VALID_SETUP.
- `multi_timeframe_policy`: 1h contexto, 15m setup, 5m confirmación; faltantes fallan cerrado.
- `liquidity_policy`: utiliza únicamente sweeps y pools/equal levels existentes; order blocks no implementados.
- `macro_filter_policy`: macro actúa como filtro de contexto; no genera dirección ni orden.
- `entry_policy`: último Close cerrado del timeframe 5m hasta `as_of`.
- `stop_policy`: invalidación estructural del SetupAssessment; no se inventa fallback.
- `target_policy`: target matemático mínimo R:R 3.0.
- `risk_handoff`: todo TradePlan se entrega al Risk Engine; Orchestrator nunca convierte VALID_SETUP directamente en APPROVED.
- `orchestrator_flow`: scouts -> SetupAssessment -> TradePlan -> RiskDecision -> FloorRunReport; no ejecución.
- `run_id_policy`: un UUID por run se propaga a scouts y reporte.
- `as_of_policy`: un instante lógico compartido por todos los scouts y planner.
- `fail_closed_policy`: datos faltantes, errores o evidencia insuficiente producen NO_SETUP/WATCH/RISK_REJECTED.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker ni dinero real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5
- `task`: Implementar Paper Broker y Trade Manager locales, deterministas y sin ejecución real.
- `tests_before`: 282 tests OK.
- `tests_after`: 284 tests OK.
- `files_created`: `execution/__init__.py`, `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `paper_account_model`: equity = starting_equity + realized_pnl + unrealized_pnl; una posición abierta por símbolo.
- `order_state_machine`: PENDING -> FILLED/CANCELLED; validación fail-closed.
- `position_state_machine`: OPEN -> CLOSED; no reapertura.
- `fill_policy`: próxima barra elegible, Open real, sin sustitución por planned entry.
- `stale_plan_policy`: orden local idempotente por run_id; no se persigue una orden duplicada.
- `gap_policy`: manager evalúa gaps antes de niveles intrabar y conserva stop-first.
- `post_fill_risk_policy`: Paper Broker valida orden aprobada; no hay broker ni dinero real.
- `RR_policy`: RiskDecision debe llegar APPROVED desde el Risk Engine.
- `PnL_policy`: PnL LONG/SHORT determinista, costes paper y journal de transiciones.
- `equity_policy`: mark-to-market sobre posiciones abiertas y PnL realizado al cerrar.
- `journal_policy`: eventos estructurados con timestamp, run_id, símbolo y entity_id.
- `idempotency_policy`: segundo submit del mismo run devuelve la orden existente.
- `no_lookahead_check`: tests de fill en próxima barra y gestión secuencial.
- `real_execution_check`: REAL_EXECUTION = DISABLED.
- `known_debt`: expiración avanzada, partial fills, persistencia y reconciliación quedan para evolución posterior.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.1 hardening
- `task`: Auditar y endurecer Paper Broker/Trade Manager antes de Fase 6.
- `tests_before`: 284 tests OK.
- `tests_after`: 286 tests OK.
- `files_created`: `AUDIT_PHASE5.md`.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: InstrumentSpec/multiplier requerido para ejecución; primera barra cerrada posterior a as_of; post-fill risk/RR/geometry fail-closed; símbolo y timestamp aislados; costes explícitos y journal estructurado.
- `warnings`: sin broker, red, credenciales ni dinero real.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.2 final execution safety gate
- `task`: Cerrar invariantes de Paper Execution con pruebas aisladas y flujo end-to-end real.
- `tests_before`: 286 tests OK.
- `tests_after`: 315 tests OK.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `core/contracts.py`, `riesgo.py`, `test_execution.py`, `AUDIT_PHASE5.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: eliminar fallback económico del multiplier; exigir timestamps aware, símbolo exacto, OHLC completo y vela cerrada; proteger opened_at/current equity; alinear LONG/SHORT en Risk Engine; propagar metadata y costes.
- `warnings`: el fixture sintético actual del Orchestrator sigue produciendo NO_SETUP; no se declara PLAN_READY sin evidencia.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4.1 hardening
- `task`: Eliminar equity hardcodeada, exigir barras cerradas en Trade Planner, validar lineage/run_id, symbol, timeframe y as_of, y conservar PLAN_UNAVAILABLE.
- `tests_before`: 278 tests OK.
- `tests_after`: 282 tests OK.
- `files_created`: ninguno.
- `files_modified`: `core/timeframes.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `agents/setup_validator.py`, `floor/orchestrator.py`, `core/contracts.py`, `test_trade_planner.py`, `test_setup_validator.py`, `test_orchestrator.py`, `CHANGELOG_AGENT.md`.
- `decisions`: equity ahora es argumento explícito del Orchestrator; equity inválida falla cerrado. Barras forming no pueden ser planned entry. Lineage y timestamps inconsistentes producen NO_SETUP. VALID_SETUP sin plan produce PLAN_UNAVAILABLE.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker, scheduler ni ejecución real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Pre-Phase-4 Audit
- `task`: Auditoría integral y hardening de la base Fase 0-3.
- `tests_before`: 253 tests OK.
- `tests_after`: 268 tests OK.
- `files_created`: `AUDIT_PRE_PHASE4.md`.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/macro_news_agent.py`, `core/contracts.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `test_macro_news.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `defects_fixed`: selección temporal incorrecta de BOS; equal highs/lows basados en barras no estructurales; Macro/News sin known_at/result_timestamp; deduplicación sin namespace de source; warning pandas de parsing temporal ambiguo.
- `known_non_blocking_debt`: dataclasses frozen con contenedores internos mutables; especificaciones contractuales desconocidas; limitaciones intradía de yfinance; warnings ScriptRunContext de Streamlit.
- `warnings`: no se encontraron secrets; no se conectó red, broker ni dinero real.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 3
- `task`: Implementar Macro/News Agent determinista con proveedor normalizado, freshness, relevancia y deduplicación.
- `tests_before`: 253 tests OK.
- `tests_after`: 266 tests OK.
- `files_created`: `data/macro_news.py`, `agents/macro_news_agent.py`, `test_macro_news.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_contract`: `MacroNewsProvider` con `macro_events()` y `news_items()`; `InMemoryMacroNewsProvider` para tests sin red.
- `timestamp_policy`: fuente/evento/publicación/recepción separados; timestamps timezone-aware normalizados a UTC; datos futuros rechazados.
- `no_lookahead_policy`: solo se aceptan datos cuyo `received_at` sea <= `as_of`; actuals sobre eventos futuros se rechazan.
- `freshness_policy`: ventanas explícitas UPCOMING 24h, ACTIVE_WINDOW 1h, RECENT 24h y STALE posterior.
- `dedup_policy`: IDs de proveedor cuando existen; fallback estable con campos disponibles.
- `relevance_policy`: categorías macro explícitas y relevancia USD/EUR/símbolo; sin causalidad ni dirección de precio.
- `warnings`: warnings legacy de Streamlit y mensajes esperados de tests; sin red ni claves API.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2.1 closure gate
- `task`: Formalizar Break of Structure, retracement y parsing temporal sin warning ambiguo.
- `tests_before`: 248 tests OK.
- `tests_after`: 253 tests OK.
- `files_created`: ninguno.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `ROADMAP.md` sin cambio de estado, `CHANGELOG_AGENT.md`.
- `BOS_definition`: Ruptura bullish/bearish por cierre de vela cerrada sobre el último swing confirmado; el swing requiere una vela cerrada posterior de confirmación y se registra evidencia completa.
- `retracement_definition`: Tras impulso confirmado y pivote protegido, el cierre actual queda entre ambos niveles sin invalidar el pivote; resultado marcado `HEURISTIC`.
- `no_lookahead_tests`: BOS y retracement usan `as_of`; barras futuras no cambian los reportes históricos.
- `pandas_warning_fix`: `pd.to_datetime(..., format="mixed")` elimina la inferencia ambigua sin silenciar warnings.
- `warnings_remaining`: warnings legítimos de ScriptRunContext de Streamlit y mensajes legacy esperados; warning temporal ambiguo de pandas eliminado.
- `next_phase`: Fase 3 — Macro/News Agent.
# Fase 6D — integración de proveedores

- Añadidos adapters Massive y OpenAI tras contratos existentes, con timeouts, reintentos acotados, salidas estructuradas, validación de barras cerradas y estados de error explícitos.
- Configuración local segura, modos de proveedor, certificador sin trading y CI de Python 3.13.
- Baseline 405 tests OK; suite ampliada 415 tests OK. Certificación en vivo pendiente de MASSIVE_API_KEY y confirmación de proyecto OpenAI. El conector Massive muestra NOT_ENTITLED para I:NDX reciente y RATE_LIMIT tras consultas adicionales; no se inicia la demo.
- Decisión posterior: enabled_symbols predeterminado XAUUSD,EURUSD; NAS100 soportado mediante I:NDX pero NOT_ENABLED/NOT_CERTIFIED. Runtime, scheduler, certificador y UI distinguen selección habilitada del catálogo. La clave OpenAI de Default project fue aceptada; 419 tests offline OK; la certificación live espera MASSIVE_API_KEY.
- Certificación live solicitada: MASSIVE_API_KEY no aparece en el .env.local indicado ni en el entorno; Massive no se consultó. OpenAI devolvió HTTP 429 RATE_LIMITED para gpt-5.6-terra. Se añadió clasificación segura de cuota agotada y prueba; 420 tests offline OK. Fase 6D sigue PARTIAL y sin commit/push.
- Reintento posterior con MASSIVE_API_KEY presente: XAUUSD referencia y tres intervalos PASS, pero STALE_DATA; EURUSD referencia PASS, pero RATE_LIMITED antes de certificar barras. NAS100 no se consultó. Massive permanece PARTIAL; OpenAI no se reintentó en esta pasada; no se hace commit/push.

# Fase 7 — Demo Runner PAPER

- Demo Runner durable por slot, preflight local READY/NOT_READY, modo diagnóstico sin órdenes y certificador offline/live.
- SQLite schema 2 agrega revisiones y notificaciones versionadas; migración desde schema 1, recuperación de ciclos interrumpidos y lectura de UI sin escritura.
- Tests E2E sintéticos cubren setup LONG/SHORT, riesgo, IA adversa, datos inválidos, fallos de proveedores, duplicados y reinicio/cierre de posición.
- Proveedores activos de la demo: Twelve Data y OpenAI; Massive soportado/no activo; NAS100 soportado/no habilitado. El experimento no se inicia.
- Certificación final local: 447 tests OK; preflight READY; diagnóstico live XAUUSD/EURUSD PASS con barras actuales, IA contractual y cero órdenes.
