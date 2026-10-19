# STATUS — AI Trading Floor V2

_Última actualización: 2026-10-18 (America/New_York; ver `CHANGELOG_AGENT.md` — hardening OANDA demo: límite diario, botón de emergencia, drift-clear, cierre del viernes, Historial; todo OFF salvo el botón)._

_Estado anterior: 2026-10-14 (America/New_York). Esta es la única fuente de estado; cualquier PR que cambie el estado debe actualizar este archivo._

**Entorno:** PAPER / DEMO únicamente · `REAL_EXECUTION_ENABLED = False` · NAS100 OFF · runtime V2 y SYSTEM HEALTH INACTIVE · sin deploy V2.
**main:** `1c9118c` (merge PR #41).

## Fases
| Fase | Nombre | Estado | PR | SHA (merge) |
|---|---|---|---|---|
| 0 | V1 Baseline / V2 Freeze | CERTIFIED | #4 | `1ab735ec` |
| 1 | Observability & Health | CERTIFIED | #6 | `1f3362f4` |
| 2 | Market Evidence | CERTIFIED | #7 | `c7aaafb4` |
| 3 | Setup Validator | CERTIFIED | #8 | `b8b7493d` |
| 4 | Trade Planner 3R | CERTIFIED | #9 | `3edb6480` |
| 5 | Risk Engine V2 | CERTIFIED | #10 | `b3e93dfc` |
| 6 | Multi-Setup / Conflict | CERTIFIED | #11 | `405df6a8` |
| 7 | AI Agent Floor | CERTIFIED | #12 | `2753130d` |
| 8 | Execution / Fill / Trade Manager | **CERTIFIED** (2026-10-10, control delegado; revisión cruzada Codex/Claude) | [#13](https://github.com/bebote0997/ai-market-system/pull/13) | `4974408e` |
| 9 | Email + Slack alerts | **CERTIFIED** (ligera, 2026-10-11; F09-T18 diferido al pre-flight de la Fase 13) | #21, #27, #31 | `7acf384` |
| 10 | Command Center / Display V2 | **CERTIFIED** (ligera, 2026-10-11; capturas móviles diferidas a la Fase 13) | #23, #31 | `7acf384` |
| 11 | Chaos / Failure Testing | **CERTIFIED** (ligera, 2026-10-11) | #29 | `d6c8ebe` |
| 12 | E2E replay determinista | **CERTIFIED** (ligera, 2026-10-11; NOT VERIFIED del verificador es por diseño) | #32 | `22af2e9` |
| 13 | Demo PAPER 30 días | **STARTED** 2026-10-14 08:29 ET (GO del owner; día 1/30, opción B, ver DEC-13.1) | — | main `1c9118c` |

Progreso certificado: 13/14 (92.86 %). Las fases 9-12 tienen certificación ligera bajo control delegado a Grok Bot, pendiente de confirmación del owner. Detalle: `docs/phases/`.

## Demo de broker OANDA (2026-10-17, borrador → PR)
- Conector OANDA practice (solo EURUSD) + `BrokerDemoRouter` detrás de `AI_FLOOR_BROKER_DEMO_ROUTING` (**apagado por defecto**; dry-run opt-in, script de una sola orden de prueba con confirmación explícita). `REAL_EXECUTION_ENABLED = False`; el host live es inalcanzable para órdenes; la vista Real del dashboard es solo lectura (GET summary). Detalle: `docs/oanda_adapter.md`.
- Seguridad: cabeceras (HSTS/CSP/X-Frame-Options/nosniff/Referrer-Policy) vía middleware ASGI, redacción de logs, ficheros SQLite 0600, sin IDs/tokens en el repo. Detalle: `docs/security_audit.md`.
- El paper trading sigue intacto y es la única ruta activa hasta que el owner active la demo de broker.
- 2026-10-19 (`feat/demo-from-zero`): con el enrutado ON el dashboard (Overview/Trades/Decisions/experimento) muestra solo el demo OANDA desde 2026-10-18 18:25 ET (`AI_FLOOR_DEMO_EPOCH`), «Día N de 30» desde la primera operación enrutada; los datos PAPER previos se archivan (copia única en `archive/`, nunca se borran). Enrutado OFF ⇒ sin cambios.
- 2026-10-19 (`feat/demo-gates-oanda`, DEC-DEMO-GATE-1): con el enrutado ON el gate PAPER de drawdown 5 % (Risk V2) usa solo el NAV OANDA practice vs la base del demo 99 999,98 (fail-closed si no se lee) y el resumen diario de Slack reporta cifras del demo OANDA. Enrutado OFF ⇒ sin cambios.
- 2026-10-19 (`feat/pause-xauusd`, DEC-XAU-OFF-1): XAUUSD pausado en el motor PAPER por código (`runtime/symbol_pause.py`); su posición PAPER abierta se cierra a mercado (simulado) con `SYMBOL_PAUSED`. EURUSD, demo OANDA, fingerprint, preflight y env sin cambios.

## Abierto (ver `docs/issues.md`)
- ISSUE-008 / ISSUE-009 (bloquean activación de SYSTEM HEALTH).
- Fase 8 cerrada: HIGH-8.1 y M-5 CLOSED. Seguimientos: ISSUE-012 (vela no cerrada en fill pendiente; adaptadores reales la filtran), ISSUE-013 (notas MEDIUM del status_verifier).
- Carry-forwards: H04 restante, H15, trazabilidad deploy/build/config/run, Email Fase 9, stash@{0}, CF-P5-OPENRISK, rollback/compatibility gate Fase 6.

## Decisiones pendientes del owner (ver `docs/decisions.md`)
- [x] DEC-8.18 — APPROVED 2026-10-10 (owner): procedimiento de verificación de estado tal cual (ejes, H5).
- [x] DEC-8.19 — APPROVED 2026-10-10 (owner): opción (a), precondición flat bajo A2.
- [x] Nuevo baseline económico/freeze tras HIGH-8.1 (A2: cuenta PAPER nueva USD 10 000, DB nueva, sealed genesis — DEC-8.20 aprobado como política) — dejado de lado por DEC-13.1 (opción B, 2026-10-14).
- [x] DEC-8.23 — APPROVED 2026-10-10: gates en vivo de Fase 8 → evidencia offline/replay; nada en vivo antes de la Fase 13; la validación en vivo diferida (G2–G5, G8, G11, G15) se hace durante el demo de 30 días de la Fase 13, tras certificar las Fases 8–12 y con aprobación operativa explícita.
- [x] DEC-8.24 — APPROVED 2026-10-10 (Grok Bot, control total delegado): G8/G15 diferidos a Fase 13 (se informa al owner).
- [x] DEC-8.18a — 2026-10-10 (Grok Bot, control delegado): sin firma H5; Q1–Q8 automatizadas (`replay/status_verifier.py`).
- [ ] DEC-8.22-a…j / OP-1…OP-7 (NOT AUTHORIZED).
- [ ] Autorizar `AI_FLOOR_CLOUD_RUNNER` / `AI_FLOOR_SCHEDULER` en Render (hoy `0`).

## Próximos pasos hacia el demo PAPER de 30 días
1. Fase 8 CERTIFIED y mergeada (PR #13).
2. Fase 9 en main (PR #21). #24 (recuperación al reinicio) y #25 (worker aislado) cerrados por la PR de `v2/phase9-alert-worker`. Pendiente: prueba real F09-T18 en Render. Fase 10 en main (PR #23). Siguiente: Fase 11 (17 escenarios de fallo como tests).
3. Owner: secretos en Render (`OPENAI_API_KEY`, `TWELVE_DATA_API_KEY`, `AI_FLOOR_DASHBOARD_PASSWORD`, opcional `SLACK_WEBHOOK_URL`) y aprobación del baseline A2.
4. Orden acordado por el owner (2026-10-10): completar y certificar Fases 8–12 (sin ninguna ejecución en vivo), luego Fase 13 = demo PAPER de 30 días. No se toca Render, flags, scheduler ni deploy hasta entonces.

## Correcciones de seguridad (2026-10-13, draft PR `v2/security-fixes`)

Fixes de la auditoría de seguridad de `main@8b11da0` (M-1, L-1..L-7, I-1..I-5): login del dashboard con rate limit, bloqueo temporal y expiración de sesión; dependencias fijadas con hashes; clave de Twelve Data en cabecera; sin email personal por defecto. Detalle en CHANGELOG_AGENT.md. **Acción del owner antes del próximo deploy:** `AI_FLOOR_DASHBOARD_PASSWORD` ≥ 12 caracteres; para email, `AI_FLOOR_ALERT_EMAIL_FROM`/`_TO`. Sin cambios en Render ni activación.

## Correcciones locales autorizadas (2026-10-11)

Alertas: evidencia 5m read-only, PNG Agg/CID y recuperación cada 60 s. Dashboard: filtros SQL antes del LIMIT y captura DASHBOARD_ERROR. Detalle y validación en fases 09/10 y CHANGELOG_AGENT.md. No cambia certificación ni activa runtime; sin commit/deploy.

## DEC-14.1 — cambio local autorizado (2026-10-11 10:00 ET)

Implementación de política opuesta detrás de `V2_OPPOSITE_SETUP_POLICY` (default `BLOCK`),
owner Jeferson Tejeda. Sin activación, deploy ni cambios en Render. Demo prevé `STRUCTURE_CHANGE`.
Pruebas deterministas nuevas y restart con abandono seguro; detalle en fase-06 y CHANGELOG_AGENT.md.
Replay original no disponible en workspace: `docs/replay/dec14_impact.txt` registra **NO_DATA**;
comparación/certificación quedan pendientes. Review fixes: planner de main restaurado, ruta nueva
solo con OPEN del mismo símbolo sin pendientes, similitud a 0.25 del stop original. Plan V1
del runtime se rechaza antes del cierre; contrato fixed-3R no relajado. Corpus original
Twelve Data ausente, no regenerable offline. Baseline: 1540 tests, cinco fallos de dashboard
y un gate histórico del broker ajustado por la adición exacta close_market; ISSUE-012 esperado.
Review final: ruff PASS; pytest 1541 passed, 5 fallos dashboard preexistentes, 1 xfailed,
1100 subtests passed. Fixes locales listos; sin certificación/activación adicional.

## Historia
El Libro Maestro en Word (versión 1.9, 2026-10-07) queda **archivado/histórico**; no se versiona aquí ni se actualiza más.

## DEC-14.2 — implementación local autorizada (2026-10-11 11:33 ET)

Owner Jeferson Tejeda: `V2_PLANNER_POLICY` implementado localmente, default V1; V2_F3 planifica 3.00R y aplica piso PAPER 2.50R en ambas rutas runtime. Demo configurará `V2_PLANNER_POLICY=V2_F3` con GO operativo; independiente de la política de setups opuestos. Sin activación, merge, deploy ni cambios en Render; Fase 13 sigue NOT STARTED.

Validación DEC-14.2: ruff y suite unittest completa PASS (1520 tests, 1 expected failure). Pytest completo: 1514 passed, 1 xfailed; 5 fallos previos del harness de señales de Fase 8 (`TestCaseFunction.failures`). Commit local pendiente de autorizar la corrección mínima de esos dos tests y obtener pytest verde.

## PR #37 — dashboard follow-up autorizado (2026-10-13)

Implementación en `v2/dashboard-luxury`: origin/main ya integrado (`8b11da0`, merge no-op).
JSON formateado/colapsable, layout móvil hasta 768px, lecturas/tablas/caché acotadas;
Setups y email opcional leen `candle_cache.db` en solo lectura, compatibles con el esquema
pre-demo de cierres 5m (no OHLC inventado). PNG de email mediante Agg, flag
`AI_FLOOR_SETUP_CHART_EMAIL` OFF por defecto. Rama pre-demo inspeccionada, no mergeada.
Detalle/evidencia en fases 09/10 y CHANGELOG_AGENT.md. PR sigue draft; sin certificación,
activación, merge a main, cambios en Render ni deploy.

Validación PR #37: suite completa 1629 tests OK (2 skipped, 1 expected failure); 58 checks
enfocados PASS, Chromium CSS y app móvil PASS, Ruff/diff-check PASS.

PR #37 History review fix (P2, 2026-10-13): tablas separadas por tipo conservan
las páginas SQL de 200 filas sin descartar evidencia. Regresión en memoria y
17 tests Phase 10 PASS; baseline local con dos errores por matplotlib ausente.
Sin cambio de certificación, activación, commit ni deploy.

Validación final del fix History: 1630 tests en 190.121 s; mismos dos errores
preexistentes por matplotlib ausente, 2 skipped, 1 expected failure; sin fallos nuevos.
Suite enfocada: 17 PASS; git diff --check PASS. Ruff no disponible en el entorno.

## Pre-demo fixes — rama `v2/pre-demo-fixes` (2026-10-13, draft PR, sin merge/deploy)

Tarea asignada por el owner: regla de duración de trade (cierre NY 17:00 ET Day-1 < +0.5R / stop a entrada,
salida Day-2 por estructura adversa, cierre forzado Day-2, viernes 17:00 ET cierra todo), macro NO_DATA con
razón clara + alerta + un reintento (sigue NO_DATA => sin trade), sin PARTIAL_AI_FAILURE falso en ciclos WATCH,
alertas de orden/posición con datos reales, caché de velas para gráficos (`AI_FLOOR_CANDLE_CACHE_PATH`
opcional). REAL OFF, NAS100 bloqueado, Fase 13 sigue NOT STARTED. Limitación conocida: el oráculo REX (solo V1)
no modela la regla de duración; los runs donde actúa salen INVALID en `replay.rex_chain`.
Validación: ruff PASS; pytest 1624 passed, 1 skipped, 1 xfailed (baseline 1590 passed, 1 skipped, 1 xfailed).
Revisión Codex (4 hallazgos: 1 HIGH, 3 MED) corregida en la misma rama: salida Day-2 por estructura en orden
temporal con SL/TP (también fuera de sesión), revalidación de frontera en la escritura CAS, precios de la regla
desde evidencia comprometida con catch-up ON, SL de la posición en alertas. pytest 1631 passed, 1 skipped, 1 xfailed.
Seguimiento (misma rama, PR #39): órdenes PENDING canceladas por tiempo en el viernes 17:00 ET o el cierre NY Day-2
tras su envío, lo que ocurra antes (`TIME_CANCEL_FRIDAY_NY_CLOSE` / `TIME_CANCEL_DAY2_NY_CLOSE` en `ORDER_CANCELLED`),
en el pase fuera de sesión y en cada ciclo antes del fill. Setup ID de alertas en la ruta de setup original: se toma
del run que envió la orden (sin registro sigue NO_DATA). pytest 1640 passed, 1 skipped, 1 xfailed (baseline 1631).

Review fixes PR #39 (2026-10-13): el catch-up de posiciones conserva `STALE_PAPER_STATE` tras dos CAS fallidos
de cancelación; no permite fills ni nuevas órdenes en ese ciclo. Signal (UTC) usa `order.as_of`, con fallback
exclusivo al review del run que envió la orden; nunca al run posterior que la cierra. Dos regresiones reproducen
los hallazgos antes del fix. Validación final registrada en CHANGELOG_AGENT.md; Fase 13 sigue NOT STARTED.

Owner-approved gold options 1 y 4 ONLY (2026-10-13, PR #39): caché macro conserva el snapshot anterior
en la frontera de refresco de 6 h, elige una versión por event ID visible al slot y sella evidencia nueva
al completar el fetch (sin lookahead). Auditoría ligera de cada ciclo reclamado en `<trading-db-stem>.cycles.db`
junto al DB: gates existentes, estado por TF, setup/rechazo, reviews AI y motivo de ejecución; retención
30 días y límite 10 000 registros, escritura best effort con timeout 100 ms. INFO a stdout en cloud_runner.
Sin cambios de alineación, estrategia, riesgo, economía, flags ni Render. Fase 13 sigue NOT STARTED;
validación en CHANGELOG_AGENT.md. Cambios locales; sin staging/commit/push, merge o deploy.

## PR #37 — integración de main (2026-10-13)

Owner autoriza merge de origin/main en `v2/dashboard-luxury`, commit y push de la rama.
Dashboard luxury conservado bajo `ui/views`, junto al hardening y fixes pre-demo de main.
Dependencias directas exactas coherentes con el hash lock; email requiere FROM/TO.
Fase 13 sigue NOT STARTED; REAL OFF, NAS100 bloqueado, sin deploy ni merge del PR.

Validación del merge: `TMPDIR=/tmp /workspace/venv/bin/python -m pytest -q` →
1721 passed, 2 skipped, 1 xfailed, 1181 subtests passed (222.50 s).
`git diff --check` PASS; conflictos resueltos. Ruff no instalado en el entorno.

## Halt/shutdown + dashboard fixes locales (2026-10-13)

Owner autoriza implementación y commits en `v2/halt-shutdown-dashboard-fixes`, sin push/merge/deploy.
SIGTERM registra `CLEAN_SHUTDOWN` con las mismas barreras de escritura y bookkeeping atómico;
otras causas conservan `HALT_OBSERVED` y el token exacto. Un shutdown no borra halts de seguridad previos.
Review fix: SIGTERM es un cierre limpio (`CLEAN_SHUTDOWN`); las señales no-SIGTERM son halts
de seguridad (`HALT_OBSERVED`). En startup y antes de comprometer bookkeeping, la causa de
seguridad tiene prioridad independientemente del orden de señales: SIGTERM nunca la degrada,
y una señal no-SIGTERM eleva un SIGTERM pendiente sin cambiar la barrera temporal original.
R realizado requiere `initial_stop` persistido (sin él, NO_DATA) y usa la cantidad propia del trade
cuando está guardada. Correcciones locales sin commit/push/deploy ni cambios de freeze.
Validación de review fixes: `/workspace/venv/bin/python -m pytest -q` → 1739 passed,
2 skipped, 1 xfailed, 1201 subtests passed (215.78 s); `git diff --check` PASS.
Dashboard: razón de cierre, R neto desde riesgo original persistido, MTM de posiciones y evidencia AI
por identidad exacta; gráficos Setups desde caché ya existente, y márgenes del eje Overview corregidos.
Legacy conservado tras inspección; trade EURUSD exacto NO_DATA, comportamiento gap caracterizado sin
cambiar economía. Hallazgos en `/tmp/legacy_findings.md` y `/tmp/eurusd_findings.md`.
Sin cambio de certificación/activación; Fase 13 sigue NOT STARTED. Validación en CHANGELOG_AGENT.md.

## Fase 13 — demo PAPER iniciado (2026-10-14 08:29 ET)

GO explícito del owner Jeferson Tejeda (DEC-13.1). Opción B: el día 1/30 arranca en la DB actual
(`data/runtime/trial/trading_floor.db`), sin DB nueva ni reinicio. Incluye la posición XAUUSD SHORT
abierta (entrada 4139.45, SL 4216.21, TP 3907.85, abierta 2026-10-14 07:40 ET) y el +USD 9.32
realizado del trial (equity 10 009.32). La desviación de DEC-8.20/DEC-8.19 (DB nueva, flat, sealed
genesis) queda aceptada por el owner. El marcador `EXPERIMENT_STARTED` del trial (freeze `8b11da0`)
se conserva; el inicio del demo queda registrado aquí. `AI_FLOOR_V2_POSITION_CATCH_UP=0` se mantiene;
`AI_FLOOR_MACRO_PROVIDER=fxmacrodata` sin cambios. Sin cambios de env/runtime/deploy.
`REAL_EXECUTION_ENABLED=False`, NAS100 bloqueado, solo PAPER. Día 30 aprox. 2026-11-12.
