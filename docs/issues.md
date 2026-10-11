# Issues y carry-forwards

Cada issue nuevo debe abrirse también como GitHub issue y enlazarse aquí.

## Abiertos
| ID | Severidad | Estado | Resumen |
|---|---|---|---|
| ISSUE-008 | Activation blocker #1 | OPEN | Latencia/bloqueo de persistencia SQLite síncrona del health sink; evaluar/mitigar o aceptar antes de activar SYSTEM HEALTH |
| ISSUE-009 | Activation blocker #2 | OPEN | Observación AI-provider inconsistente (TIMEOUT vs CONNECTION_ERROR vs UNKNOWN_PROVIDER_FAILURE); una sola ruta autoritativa |
| HIGH-8.1 | HIGH | **CLOSED** (983d2d2; revisión Codex PASS WITH NOTES) | Catch-up de snapshot 5m saltaba SL/TP intermedios en ruta OFF |
| M-5 | MEDIUM | **CLOSED** 2026-10-10 | R-HALT halt/recovery: PR #16; revisión Codex PASS (ronda 2) |
| ISSUE-012 | LOW | OPEN | `progress_pending` (`runtime/service.py`) no filtra una vela 5m no cerrada; los adaptadores reales sí (`data/twelve_data_provider.py`, `data/massive_provider.py`). Test `expectedFailure` en `test_high81_runtime.py` (PR #19) |
| ISSUE-013 | MEDIUM | OPEN | Notas de revisión de `replay/status_verifier.py` (PR #18): guard `--out` sin sidecars -wal/-shm, alternativa S1 genesis no implementada, tests M8/E5/E6 faltantes |

## Carry-forward
- H04 restante (clasificación de ciclos lentos).
- H15 builds deterministas (obligatorio antes del gate de deploy).
- Trazabilidad deploy/build/config/run (ISSUE-004 como requisito V2).
- Email Engine completo → Fase 9 (ISSUE-007: Chart, Risk %, Session, Quantity).
- Rollback MEDIUM Fase 6; compatibility gate antes de runtime.
- CF-P5-OPENRISK; NAS100; legacy pending (Fase 5).
- Fase 3: re-entrada tras cierre del mismo setup; aging de ventana de invalidación.
- `stash@{0}` preservado en Fase 2: inspeccionar antes de borrar.

## Cerrados (referencia)
ISSUE-001…007 (Fase 0, 2026-10-02); ISSUE-010 (Fase 2).
