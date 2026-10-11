# Fase 11 — Chaos / Failure Testing

**Estado:** CERTIFIED (ligera, PLAN_RAPIDO; 2026-10-11, control delegado a Grok Bot, pendiente de confirmación del owner).

**Alcance:** `test_phase11_chaos.py`, 17 tests en el orden exacto de la checklist F11 del Libro v1.9
(`/workspace/libro.txt`, sección F11). Harness local con DemoRunner/OperationalRuntime, proveedores/fakes
existentes, reloj determinista y SQLite en directorio temporal. Sin red, dependencias nuevas, cambios de
producción, semántica de trading ni activación de flags V2. Conflicto/Risk V2 se llaman directamente offline.

Cada escenario comprueba el resultado/registro esperado, el estado económico permitido por la política y
recuperación o idempotencia. Las comparaciones usan el estado durable de cuenta, posiciones, órdenes y fills;
la gestión normal de una posición puede actualizar mark-to-market y cerrarla por TP.

## Mapping y evidencia

Archivo: `test_phase11_chaos.py`, clase `Phase11ChaosTests`. Todos los métodos llevan el prefijo `test_chaos_`.
Resultado focal: **17/17 PASS**, sin nuevos expected failures.

| ID | Sufijo del método | Inyección → resultado esperado y observado (PASS) |
|---|---|---|
| F11-T01 | `F11_T01_openai_quota_exhausted` | HTTP 429 quota en transporte OpenAI fake → `QUOTA_EXHAUSTED`, `PROVIDER_FAILURE`, ejecución `POLICY_REVIEW_UNAVAILABLE`; sin orden; recarga permite una orden. |
| F11-T02 | `F11_T02_openai_unavailable` | URLError en transporte fake → `CONNECTION_ERROR`, mismo bloqueo de ejecución; proveedor restaurado permite una orden. |
| F11-T03 | `F11_T03_invalid_provider_response` | Prosa en lugar de AIResponse → `INVALID_SCHEMA`, mismo bloqueo; respuesta determinista válida recupera. |
| F11-T04 | `F11_T04_market_data_unavailable` | Error tipado HTTP 503 del fake de mercado → `NO_DATA` + `PROVIDER_FAILURE`; estado intacto; siguiente slot sano permite una orden. |
| F11-T05 | `F11_T05_market_data_stale` | Barras con 180 minutos de antigüedad → `STALE_DATA` + `DATA_STALE`; estado intacto; datos frescos recuperan. |
| F11-T06 | `F11_T06_macro_unavailable` | OSError en macro → `NO_SETUP` + `PROVIDER_FAILURE`; estado intacto; macro restaurada recupera. |
| F11-T07 | `F11_T07_email_unavailable` | SMTP fake inaccesible → ledger `FAILED` / `CONNECTION_ERROR`; AlertNotificationSink durante ciclo real conserva la única orden autorizada; SMTP restaurado entrega evento nuevo. |
| F11-T08 | `F11_T08_sqlite_error` | Trigger SQLite aborta INSERT de review → IntegrityError explícito, rollback y run/lock RUNNING; tras reparar, recuperación durable `FAILED` / `interrupted_run` + `STATE_INCONSISTENCY`; mismo slot no se repite y siguiente slot recupera. |
| F11-T09 | `F11_T09_persistent_disk_issue` | ENOSPC al escribir ORDER_SUBMITTED en dos slots consecutivos → `ERROR` + `RUN_FAILED`; rollback económico completo; tras retirar fallo, una orden sin duplicación. |
| F11-T10 | `F11_T10_dashboard_error` | Lectura de snapshot UI falla → OperationalError explícito, sin fallback inventado ni escritura; lector restaurado muestra la orden durable; mismo slot duplicado no muta estado. |
| F11-T11 | `F11_T11_scheduler_restart` | Scheduler nuevo en mismo slot → `DUPLICATE`, sin cambios; salto de slots registra `SLOTS_MISSED`, llena una vez; siguiente tick duplicado no cobra costes de nuevo. |
| F11-T12 | `F11_T12_render_restart` | KeyboardInterrupt antes del submit + conexiones nuevas (simulación de reinicio, no Render real) → `FAILED` / `interrupted_run` + `STATE_INCONSISTENCY`; recuperación crea una orden; nuevo reinicio conserva orden y un único fill. |
| F11-T13 | `F11_T13_existing_position` | Posición LONG real del broker ya abierta → `EXISTING_POSITION` / `EXECUTION_DECISION`; sin orden adicional ni cambio de geometría; gestión posterior cierra por TP una vez. |
| F11-T14 | `F11_T14_multiple_positions` | Dos posiciones por símbolos habilitados con órdenes originarias FILLED → mismo bloqueo en XAUUSD; ambas geometrías y fills conservados; TP cierra solo XAUUSD, EURUSD permanece. |
| F11-T15 | `F11_T15_two_simultaneous_setups` | Interleaving determinista de dos conexiones: candidato competidor escribe antes del CAS → una reserva; perdedor reevalúa y registra `SAME_DIRECTION_PENDING_CONFLICT`; retry ganador `DUPLICATE_RUN`, estado intacto. |
| F11-T16 | `F11_T16_opposite_setup` | Setup SHORT frente a LONG abierto → `EXISTING_POSITION`, sin reverse/hedge ni modificación de SL/TP/cantidad; gestión normal sigue y cierra por TP una vez. |
| F11-T17 | `F11_T17_risk_limit_reached` | Equity en umbral de drawdown del 5% → `RISK_REJECTED` / `ACCOUNT_DRAWDOWN_LIMIT` + `RISK_V2_DECISION`; sin orden ni mutación; cuenta fixture reparada permite reserva idempotente. |

**Precisiones de política:** fallo total de IA conserva el reporte analítico `PLAN_READY`, pero bloquea la
**ejecución** con `POLICY_REVIEW_UNAVAILABLE`. Email/dashboard son observadores: su fallo no invalida una
orden previamente autorizada. Si SQLite impide también registrar el fallo, se conserva el lock hasta recuperar;
no se exige un journal imposible durante el fallo. ENOSPC se modela en la escritura transaccional económica;
no se llena un disco real. T15 prueba la intercalación CAS de forma determinista, sin carrera dependiente del reloj.
La reparación de drawdown es exclusivamente una fixture, nunca una política de reset de runtime.

## Validación y gate

- Baseline antes de cambios: **1475 tests OK (1 expected failure existente)**.
- Suite completa: **1492 tests OK (1 expected failure existente)**, 155.521 s.
- `/workspace/repo/.venv/bin/ruff check .` y `git diff --check`: **PASS**.
- Comando y evidencia final registrados en `CHANGELOG_AGENT.md`.
- Sin bugs nuevos detectados, fixes de producción ni nuevos ISSUE/expectedFailure.

**Gate de certificación:** requiere fase anterior CERTIFIED, revisión independiente y aprobación explícita
del owner. La autorización de issue #28 permite este trabajo offline; no declara Fase 10/11 certificada,
ni autoriza runtime, deploy o Render. PAPER/DEMO, NAS100 OFF y flags V2 OFF se preservan.


## Certificación ligera (2026-10-11)
- [x] PR #29: 17 tests `test_chaos_F11_T01…T17` (https://github.com/bebote0997/ai-market-system/pull/29), merge d6c8ebe, CI https://github.com/bebote0997/ai-market-system/actions/runs/38116210242. Autor Codex; revisión de Grok Bot: APPROVE (3 LOW).
- [x] Ningún fallo crea órdenes fuera de la política; la recuperación y la idempotencia están verificadas.
