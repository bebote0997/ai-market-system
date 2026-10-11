# STATUS — AI Trading Floor V2

_Última actualización: 2026-10-10 (America/New_York). Esta es la única fuente de estado; cualquier PR que cambie el estado debe actualizar este archivo._

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
| 9 | Email + Slack alerts (OFF por defecto) | IMPLEMENTED, merge en main (PR #21, 4452350); no certificada: faltan F09-T18 (envío real en Render), #24 y #25 | — | — |
| 10 | Command Center / Display V2 | IN PROGRESS (issue #22, PR #23; revisión Grok Bot REQUEST_CHANGES, hallazgos MEDIUM/LOW corregidos localmente; pendiente re-revisión y certificación) | — | — |
| 10–13 | Command Center, Chaos, E2E, demo PAPER 30 días | NOT STARTED | — | — |

Progreso certificado: 9/14 (64.29 %). Detalle: `docs/phases/`.

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
2. Fase 9 en main (PR #21). Pendiente: #24 (claims obsoletos tras reinicio), #25 (worker aislado), prueba real F09-T18 en Render. Fase 10: PR #23 abierta; revisión independiente de Grok Bot REQUEST_CHANGES atendida localmente (ledger F9 read-only, consultas acotadas/historial paginado, fallback y limpieza). Pendientes re-revisión, revisión visual y certificación.
3. Owner: secretos en Render (`OPENAI_API_KEY`, `TWELVE_DATA_API_KEY`, `AI_FLOOR_DASHBOARD_PASSWORD`, opcional `SLACK_WEBHOOK_URL`) y aprobación del baseline A2.
4. Orden acordado por el owner (2026-10-10): completar y certificar Fases 8–12 (sin ninguna ejecución en vivo), luego Fase 13 = demo PAPER de 30 días. No se toca Render, flags, scheduler ni deploy hasta entonces.

## Historia
El Libro Maestro en Word (versión 1.9, 2026-10-07) queda **archivado/histórico**; no se versiona aquí ni se actualiza más.
