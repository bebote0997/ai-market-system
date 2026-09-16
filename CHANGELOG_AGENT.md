# Agent Changelog

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
