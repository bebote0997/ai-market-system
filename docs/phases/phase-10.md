# Fase 10 — Command Center / Display V2

**Estado:** CERTIFIED (ligera, PLAN_RAPIDO; 2026-10-11, control delegado a Grok Bot, pendiente de confirmación del owner).

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


## Certificación ligera (2026-10-11)
- [x] PR #23: Command Center (https://github.com/bebote0997/ai-market-system/pull/23), merge 3065fdb, CI https://github.com/bebote0997/ai-market-system/actions/runs/38114938843. Autor Codex; revisión independiente de Grok Bot en 2 rondas (3 MEDIUM corregidos, luego APPROVE).
- [x] PR #31: filtros antes del LIMIT y DASHBOARD_ERROR, merge 7acf384.
- [x] El acceso a SQLite es `mode=ro` (los tests comprueban el hash intacto), con contraseña (`AI_FLOOR_DASHBOARD_PASSWORD`) y tests de humo para cada página.
- [ ] **Diferido al pre-flight de la Fase 13:** capturas en móvil (iPhone) y escritorio, y revisión visual del owner sobre el despliegue en Render.

## PR #37 — JSON, mobile, memory y cache (2026-10-13)

Owner autoriza actualizar `v2/dashboard-luxury`; `git merge origin/main` → Already up to date
(`8b11da0`). JSON escapado/indentado/colapsable, IDs/estado primero; tablas 200 filas y detalle
JSON de 20. CSS fuerza columnas únicas hasta 768px; Chromium verifica 375/640/768/1024px.
Process consulta 200 ciclos, conservando links exactos a ciclos anteriores; Setups filtra validación
antes del LIMIT y renderiza un solo setup seleccionado. Estadísticas exactas sobre todo el ledger
con campos escalares por cursor, curva limitada a 200 puntos; legacy no carga fills/trades completos.

`runtime/setup_candle_reader.py`: adapter read-only del esquema de `origin/v2/pre-demo-fixes`,
sin merge. Ruta `AI_FLOOR_CANDLE_CACHE_PATH`, o `candle_cache.db` junto a `AI_FLOOR_DB_PATH`.
48 cierres 5m del símbolo, cerrados a `as_of`; gráfico de línea etiquetado **no OHLC**, con
Entry/SL/TP y labels del plan. Caché UI TTL 30s / max_entries 32. Ausencia/error → OHLC 15m
persistido en ciclo exacto, o unavailable; no fetch ni datos inventados.

Validación enfocada: 58 tests PASS; Chromium CSS PASS; Ruff/diff-check PASS. Baseline venv:
1614 tests OK (1 skipped, 1 expected failure). Cierre full-suite en CHANGELOG_AGENT.md.
PR continúa draft; no certificación ni deploy.

Cierre PR #37: suite completa 1629 tests OK (2 skipped, 1 expected failure), 190.416s.
Chromium CSS y app real local con fixtures: PASS; Ruff/diff-check: PASS.

## PR #37 — History pagination review fix (P2, 2026-10-13)

History muestra una tabla por tipo de registro, cada una con su página SQL de
hasta 200 filas. El cap de presentación ya no elimina evidencia detrás de runs.
Regresión en memoria: 201 registros de cada uno de los ocho tipos; dos páginas
completas sin solapamiento, filtros exactos combinados y páginas vacías. Falla
contra el render original y pasa corregida. Suite Phase 10: 17 tests PASS.
Baseline local: 1629 tests, dos errores previos por matplotlib ausente, 2 skipped,
1 expected failure. Sin certificación, commit, push, activación ni deploy.

Validación final del fix History: 1630 tests en 190.121 s; mismos dos errores
preexistentes por matplotlib ausente, 2 skipped, 1 expected failure; sin fallos nuevos.
Suite enfocada: 17 PASS; git diff --check PASS. Ruff no disponible en el entorno.

## PR #37 — merge de main autorizado (2026-10-13)

Vistas luxury, Process y Setups migradas a `ui/views`, conservando funciones y navegación.
Se integra auth endurecida de main y fixes pre-demo; imports y fixture de email adaptados
al contrato vigente. Dependencias exactas alineadas con requirements-ci.txt.
No cambia certificación ni activa runtime; sin deploy ni merge del PR.

Validación del merge: `TMPDIR=/tmp /workspace/venv/bin/python -m pytest -q` →
1721 passed, 2 skipped, 1 xfailed, 1181 subtests passed (222.50 s).
`git diff --check` PASS; conflictos resueltos. Ruff no instalado en el entorno.

## Fix local autorizado — datos luxury (2026-10-13)

Trades muestra reason y realized_r. R neto usa la posición persistida por ID, initial_stop si existe,
entry_price, quantity y contract_multiplier; sin denominador comprobable sigue ausente.
Avg R conserva población completa y streaming con join por PK; páginas limitadas a 200.
Open positions muestra quantity y MTM desde last_price persistido. Decisions usa campos existentes y
fallback del snapshot del mismo run/agente, sin sobrescribir valores disponibles; joins de padres acotados.
Overview reserva margen y automargin para labels. Setups ya usa ui/candle_cache.py (48 cierres 5m
anteriores al setup, solo lectura); regresión render confirma caché sin OHLC inventado.
Legacy no eliminado: funciones distintas/documentadas en /tmp/legacy_findings.md.
No cambia certificación/activación; sin push/merge/deploy.
