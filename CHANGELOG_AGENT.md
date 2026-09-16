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
