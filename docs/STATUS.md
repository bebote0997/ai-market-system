# STATUS — AI Trading Floor V2

_Última actualización: 2026-10-11 (America/New_York). Esta es la única fuente de estado; cualquier PR que cambie el estado debe actualizar este archivo._

**Entorno:** PAPER / DEMO únicamente · `REAL_EXECUTION_ENABLED = False` · NAS100 OFF · runtime V2 y SYSTEM HEALTH INACTIVE · sin deploy V2.
**main:** `4974408e443908ceb5723aed13c1eb15a81f3c51` (merge PR #13, Fase 8).

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
| 13 | Demo PAPER 30 días | NOT STARTED: requiere el GO del owner; ver `docs/phases/phase-13-runbook.md` | — | — |

Progreso certificado: 13/14 (92.86 %). Las fases 9-12 tienen certificación ligera bajo control delegado a Grok Bot, pendiente de confirmación del owner. Detalle: `docs/phases/`.

## Abierto (ver `docs/issues.md`)
- ISSUE-008 / ISSUE-009 (bloquean activación de SYSTEM HEALTH).
- Fase 8 cerrada: HIGH-8.1 y M-5 CLOSED. Seguimientos: ISSUE-012 (vela no cerrada en fill pendiente; adaptadores reales la filtran), ISSUE-013 (notas MEDIUM del status_verifier).
- Carry-forwards: H04 restante, H15, trazabilidad deploy/build/config/run, Email Fase 9, stash@{0}, CF-P5-OPENRISK, rollback/compatibility gate Fase 6.

## Decisiones pendientes del owner (ver `docs/decisions.md`)
- [x] DEC-8.18 — APPROVED 2026-10-10 (owner): procedimiento de verificación de estado tal cual (ejes, H5).
- [x] DEC-8.19 — APPROVED 2026-10-10 (owner): opción (a), precondición flat bajo A2.
- [ ] Nuevo baseline económico/freeze tras HIGH-8.1 (A2: cuenta PAPER nueva USD 10 000, DB nueva, sealed genesis — DEC-8.20 aprobado como política).
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
