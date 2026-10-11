# STATUS — AI Trading Floor V2

_Última actualización: 2026-10-10 (America/New_York). Esta es la única fuente de estado; cualquier PR que cambie el estado debe actualizar este archivo._

**Entorno:** PAPER / DEMO únicamente · `REAL_EXECUTION_ENABLED = False` · NAS100 OFF · runtime V2 y SYSTEM HEALTH INACTIVE · sin deploy V2.
**main:** `2753130d22abc33e12caf766e8ed5dfba17a8b70` (merge PR #12).

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
| 8 | Execution / Fill / Trade Manager | **IN PROGRESS — DRAFT PR, NOT CERTIFIED** | [#13](https://github.com/bebote0997/ai-market-system/pull/13) | HEAD `983d2d24` |
| 9–13 | Email, Command Center, Chaos, E2E, Long PAPER | NOT STARTED | — | — |

Progreso certificado: 8/14 (57.14 %). Detalle: `docs/phases/`.

## Abierto (ver `docs/issues.md`)
- ISSUE-008 / ISSUE-009 (bloquean activación de SYSTEM HEALTH).
- Fase 8: M-5 OPEN; revisión independiente de HIGH-8.1; checklist PREPARATION ONLY.
- Carry-forwards: H04 restante, H15, trazabilidad deploy/build/config/run, Email Fase 9, stash@{0}, CF-P5-OPENRISK, rollback/compatibility gate Fase 6.

## Decisiones pendientes del owner (ver `docs/decisions.md`)
- [ ] DEC-8.18 — procedimiento de verificación de estado (ejes FS/EX…, atestación H5).
- [ ] DEC-8.19 — boundary handling (bajo A2: precondición flat).
- [ ] Nuevo baseline económico/freeze tras HIGH-8.1 (A2: cuenta PAPER nueva USD 10 000, DB nueva, sealed genesis — DEC-8.20 aprobado como política).
- [ ] DEC-8.22-a…j / OP-1…OP-7 (NOT AUTHORIZED).
- [ ] Autorizar `AI_FLOOR_CLOUD_RUNNER` / `AI_FLOOR_SCHEDULER` en Render (hoy `0`).

## Próximos pasos hacia el demo PAPER de 30 días
1. Owner: DEC-8.18 + copia de DB + atestación H5; DEC-8.19.
2. Revisión independiente y certificación de Fase 8 sobre PR #13; luego merge autorizado.
3. Owner: secretos en Render (`OPENAI_API_KEY`, `TWELVE_DATA_API_KEY`, `AI_FLOOR_DASHBOARD_PASSWORD`, opcional `SLACK_WEBHOOK_URL`) y aprobación del baseline A2.
4. Arranque PAPER autorizado. Nota: el Libro define Fase 13 como 60 días con auditoría Day 30; un demo de 30 días equivale a la primera mitad salvo nueva decisión.

## Historia
El Libro Maestro en Word (versión 1.9, 2026-10-07) queda **archivado/histórico**; no se versiona aquí ni se actualiza más.
