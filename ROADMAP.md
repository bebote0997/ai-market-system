# Trading Floor V2 — Roadmap

## Fase 0 — COMPLETADA

Auditoría del prototipo, baseline de tests, motor técnico, backtesting histórico, estrategia experimental, simulador, riesgo V1 y validación fuera de muestra.

Resultado conocido: baseline actual de `230 tests OK` antes de esta documentación.

## Fase 1 — COMPLETADA: contratos, datos y LONG/SHORT

Crear contratos versionados, timeframes, resolver de símbolos y proveedor de datos desacoplado. Adaptar el Risk Engine para LONG/SHORT y formalizar validaciones de SL/TP, R:R, sizing y límites.

Resultado implementado:

- Contratos versionados `InstrumentSpec`, `MarketBar`, `TradePlan` y `RiskDecision`.
- Timeframes autorizados `1h`, `15m`, `5m` y timezone UTC.
- Catálogo explícito para `XAUUSD`, `NAS100` y `EURUSD`; especificaciones contractuales desconocidas quedan como `None` y el Risk Engine rechaza si son necesarias.
- Risk Engine V2 con LONG/SHORT, límite de riesgo, R:R mínimo y estados `APPROVED`/`REJECTED`.
- Ruta explícita de simulación V2 con SHORT; la ruta legacy permanece compatible.
- Tests de integración TradePlan -> Risk Engine -> simulación para aprobación y rechazo.

Aceptación:

- `XAUUSD`, `NAS100/US100` y `EURUSD` tienen configuración explícita.
- `1H`, `15M` y `5M` están representados formalmente.
- Datos incluyen timezone, fuente, calidad y vela cerrada.
- Risk Engine soporta LONG y SHORT.
- Riesgo máximo y R:R se validan determinísticamente.
- El simulador legacy sigue pasando sus tests.
- No hay broker ni órdenes reales.

Todos los criterios de Fase 1 están cumplidos. La suite pasó 237 tests.

## Fase 2 — COMPLETADA: Structure y Liquidity Agents

Implementar primero definiciones deterministas y testeables de swings, estructura, desplazamiento, retrocesos, liquidity sweeps y order blocks. Etiquetar heurísticas explícitamente.

Resultado implementado:

- `agents/structure_agent.py` determinista con swings, HH/HL/LH/LL, bias, niveles, desplazamiento heurístico y corte temporal `as_of`.
- `agents/liquidity_agent.py` determinista con equal highs/lows, pools y sweeps; equal-leveles marcados como `HEURISTIC`.
- Contrato `AgentMessage` versionado con estado, evidencia, calidad de datos y warnings.
- Soporte explícito para `1h`, `15m`, `5m`, UTC, velas cerradas y estados `OK`, `PARTIAL`, `NO_DATA`, `ERROR`.
- Tests de multi-timeframe, datos incompletos, duplicados y no-lookahead.

Aceptación: cumplida con `248 tests OK`. No se implementaron order blocks arbitrarios, señales ni órdenes.

## Fase 3 — COMPLETADA: Macro/News Agent

Añadir proveedor de noticias/calendario con fuente, timestamp y calidad. La ausencia de información debe producir `NO_DATA`, nunca contexto inventado.

Resultado implementado:

- Contratos versionados `MacroEvent`, `NewsItem` y `MacroNewsReport`.
- `MacroNewsProvider` y `InMemoryMacroNewsProvider` sin dependencia de red.
- Normalización UTC y separación entre event/published/source/received timestamps.
- Freshness `UPCOMING`, `ACTIVE_WINDOW`, `RECENT`, `STALE`.
- Relevancia determinista para USD/EUR y símbolos objetivo.
- Impacto desconocido como `UNKNOWN`, sin inventar `HIGH`.
- Deduplicación determinista por ID o campos disponibles.
- Estados `OK`, `PARTIAL`, `NO_DATA`, `ERROR` y protección contra look-ahead.

Aceptación: cumplida con `266 tests OK`. No se implementaron componentes de Fase 4.

Aceptación: ninguna noticia sin fuente; datos stale o ausentes quedan visibles como warnings/`NO_DATA`.

## Fase 4 — COMPLETADA: Setup Validator, Trade Planner y Orchestrator

Conectar los tres scouts en un orquestador. Producir `NO_SETUP`, `WATCH` o `VALID_SETUP` y planes LONG/SHORT estructurados. Todo plan pasa por Risk Engine.

Resultado implementado:

- `SetupAssessment` y `FloorRunReport` versionados.
- Setup Validator determinista con `NO_SETUP`, `WATCH`, `VALID_SETUP`.
- Trade Planner LONG/SHORT con entrada desde Close cerrado, invalidación estructural y R:R base 3.
- Orchestrator con un único `run_id` y `as_of`, scouts 1h/15m/5m y handoff explícito al Risk Engine.
- Risk `APPROVED`/`REJECTED` propagado sin ejecución de broker.

Aceptación: cumplida con `278 tests OK`; ejecución deshabilitada y sin componentes de Fase 5.

## Pre-Phase-4 Audit — PASSED

Auditoría integral completada antes de iniciar Fase 4. BOS, retracement, liquidez, Macro/News, no-lookahead, timestamps y deduplicación fueron revisados con `268 tests OK`. La deuda restante está documentada en `AUDIT_PRE_PHASE4.md` y no bloquea el inicio de Fase 4.

## Fase 5 — Paper Execution y Trade Manager

Crear Paper Broker, estados de órdenes y Trade Manager para fills, cancelaciones, SL/TP y posiciones. Sin conexión real.

Aceptación: idempotencia, estados reproducibles, recuperación ante errores y cero órdenes reales.

## Fase 6 — Floor Assistant y Trading Floor UI

Construir resumen operativo y UI especializada con evidencia, calidad de datos, setups, riesgo, órdenes paper, posiciones y eventos. Mantener el dashboard legacy separado durante la transición.

Aceptación: cada decisión se puede reconstruir; no hay recomendaciones ocultas ni datos inventados.

## Regla de avance

Cada fase debe producir algo ejecutable y verificable con tests. No optimizar parámetros con resultados de test ni adelantar fases sin autorización explícita.
