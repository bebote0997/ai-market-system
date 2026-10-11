# Fase 10 — Command Center / Display V2

**Estado:** IN PROGRESS (2026-10-11). Implementación local autorizada por el owner para [issue #22](https://github.com/bebote0997/ai-market-system/issues/22); sin certificación, merge, deploy ni activación.

Extensión de `ui/`: Overview; posiciones abiertas y órdenes pendientes de todos los símbolos; últimos setups por símbolo; decisiones AI y Risk Engine; salud, provider/API y scheduler; entregas recientes; historial con búsqueda exacta por run_id/setup_id; errores. Se conservan FLOOR, MARKETS, JOURNAL, EXPERIMENT y SYSTEM.

SQLite se abre con URI `mode=ro`, sin migrar esquemas ni crear DBs ausentes. Alertas muestran el ledger Fase 9 `alert_deliveries` cuando `AI_FLOOR_ALERTS_LEDGER_PATH` apunta a un archivo existente, además de V1 `notification_deliveries` y su evento relacionado. Cada fuente ausente/vacía muestra `NO_DATA`; no se instancia el dispatcher ni se crea/migra el ledger. La salud lee estados publicados y, si existe, el sidecar `system_health.db` (override `AI_FLOOR_HEALTH_DB_PATH`) en solo lectura. La configuración de un provider no acredita que su API esté saludable. No se ejecutan probes. Los IDs se buscan únicamente en campos persistidos, incluyendo payloads anidados; no se inventan IDs para setups legacy. Los datos numéricos se muestran tal como fueron persistidos.

Password gate existente (`AI_FLOOR_DASHBOARD_PASSWORD`): requerido en remoto y también localmente cuando está configurado. El gate precede el acceso a SQLite. Diseño dark existente con columnas apiladas a ≤640px; sin controles operativos. Las páginas Command Center siempre muestran evidencia persistida; SAMPLE / DEMO aplica a las páginas legacy de snapshots.

## F10-T01…T22 → evidencia automatizada

Mapa de cobertura de las tareas del Libro v1.9 al alcance local del issue. Tests en `test_phase10_dashboard.py`, clase `Phase10DashboardTests`:

| ID | Cobertura | Test / evidencia |
|---|---|---|
| F10-T01 | Equity | `test_every_page_renders_and_database_hash_unchanged` (valor exacto de fixture) |
| F10-T02 | Realized PnL | mismo test (valor exacto) |
| F10-T03 | Unrealized PnL | mismo test (valor exacto) |
| F10-T04 | Open positions | mismo test (posición persistida) |
| F10-T05 | Pending orders | mismo test (orden persistida) |
| F10-T06 | Current setups | mismo test (dos símbolos) |
| F10-T07 | Market state | mismo test (snapshot persistido / Overview y MARKETS) |
| F10-T08 | AI decisions | mismo test (agente persistido) |
| F10-T09 | Risk decisions | mismo test (`REJECTED` persistido) |
| F10-T10 | System Health | mismo test (sidecar y hash intacto) |
| F10-T11 | Recent alerts | mismo test (`FAILED`); `test_missing_delivery_table_and_missing_database_show_no_data` |
| F10-T12 | Run history | `test_search_run_id_setup_id_and_combination` |
| F10-T13 | Setup history | mismo test (plan, órdenes/posiciones y journal con identidad) |
| F10-T14 | Search run_id | mismo test (exacto, combinación AND, entrada tipo SQL) |
| F10-T15 | Search setup_id | mismo test (exacto, combinación sin coincidencias) |
| F10-T16 | Charts | smoke de Overview/FLOOR/MARKETS; `test_ui.TestFloorUI.test_missing_data_and_sample_isolation` verifica figura existente y ausencia de barras |
| F10-T17 | Errors | `test_every_page_renders_and_database_hash_unchanged` (run fallido y journal) |
| F10-T18 | Provider health | mismo test (sidecar persistido) |
| F10-T19 | API status | mismo test (`NO_DATA` explícito) |
| F10-T20 | Scheduler status | mismo test (`DISABLED` persistido) |
| F10-T21 | iPhone / desktop | `test_mobile_layout_rules_and_local_password_gate` (regla responsive); smoke AppTest de todas las páginas |
| F10-T22 | Dark / clean y seguridad de display | tema existente + regla responsive; `test_password_gate_precedes_database_access`, `test_sqlite_connection_rejects_writes`; hashes tras renders y búsquedas |

`test_empty_fixture_every_page_renders` recorre también todas las páginas sobre una DB vacía válida. No se añaden dependencias. El smoke no sustituye revisión visual en un iPhone/desktop real; esa revisión, la revisión independiente y la aprobación/certificación siguen pendientes. Si no hay barras en el snapshot persistido, el chart muestra `NO_DATA`.

## Validación

- Baseline: 1426 tests PASS, 1 expected failure (170.858s).
- Phase 10 enfocada: 7 tests PASS (2.373s).
- Cierre: `PYTHONDONTWRITEBYTECODE=1 AI_FLOOR_MARKET_PROVIDER=none AI_FLOOR_AI_PROVIDER=deterministic /workspace/repo/.venv/bin/python -m unittest discover -q`: 1433 tests PASS, 1 expected failure (169.791s).
- `/workspace/repo/.venv/bin/ruff check .` y `git diff --check`: PASS.

**Gate:** requiere fase anterior CERTIFIED y aprobación explícita del owner para certificación/activación. La autorización de esta tarea permite implementación local; no cambia flags, trading, riesgo, broker, scheduler, Render ni economía.

## PR #23 — revisión Grok Bot (2026-10-11)

Hallazgos MEDIUM/LOW atendidos localmente, pendientes de re-revisión independiente:
- STATUS conserva Phase 9 de main; solo se actualizan la fila y próximos pasos Phase 10.
- Ledger Phase 9 en `mode=ro`, junto a V1; tests de campos de canal/intentos/recuperación, hash intacto, escrituras rechazadas y NO_DATA sin crear archivos.
- Consultas limitadas a 200 filas con ORDER BY determinista; historial filtra IDs exactos en SQL (columnas, JSON anidado y run asociado) antes del LIMIT, con paginación por tabla. Tests con más de 200 filas, páginas disjuntas, filtros antiguos, AND y parent fuera de la página reciente.
- DB sin `system_state`: símbolos DEFAULT y app operativa. Se elimina `ui/pages/positions.py`; POSITIONS usa Command Center y conserva cobertura de navegación.
- Regresiones: 13 tests Phase 10 PASS (6 nuevos). Suite completa solicitada: 1481 tests OK, 1 expected failure (183.177s); Ruff y git diff --check PASS. Evidencia registrada también en CHANGELOG_AGENT.md.

No certificación, activación, merge, deploy ni commit.


## Small fixes autorizados — 2026-10-11

- POSITIONS filtra `OPEN` y órdenes `PENDING` mediante `json_extract(payload, '$.status')` en SQL antes de LIMIT. ERRORS filtra runs `FAILED` o error no vacío y journal `ERROR`/`CRITICAL` en SQL antes de LIMIT; registros antiguos elegibles ya no se pierden por filas recientes no elegibles.
- Tests con mocks comprueban predicados y parámetros antes del LIMIT, cableado de render y T10: `sqlite3.OperationalError` durante lectura se muestra como `DASHBOARD_ERROR`.
- Sin cambio de estado de certificación, activación, deploy ni commit.

Validación final: suite completa solicitada, 1495 tests OK (1 expected failure), 176.371 s; Ruff y git diff --check PASS.
