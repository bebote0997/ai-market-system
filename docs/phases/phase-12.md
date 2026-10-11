# Fase 12 — End-to-End Certification

**Estado:** CERTIFIED (ligera, PLAN_RAPIDO; 2026-10-11, control delegado a Grok Bot, pendiente de confirmación del owner).

**Tareas (Libro v1.9):** Cada transición DATA→ANALYSIS→SETUP→PLAN→TARGET/R:R→RISK→AI→POSITION CONFLICT→EXECUTION→ORDER→FILL→POSITION→MANAGEMENT→EXIT→EMAIL→DASHBOARD→JOURNAL, y auditar cada transición y sus fallos (F12-T01…T17).

**Gate:** requiere fase anterior CERTIFIED y autorización explícita del owner.

## Implementación offline

`test_phase12_e2e.py`, clase `Phase12E2ETests`: ocho ciclos de DemoRunner en cuatro slots
de 15 minutos, XAUUSD LONG y EURUSD SHORT. Barras sintéticas locales de 5m, agregadas a
15m/1h y filtradas por `replay.engine.snapshot_at` (inicio + duración <= slot).
Scouts inyectan evidencia estructurada explícita, siguiendo el harness F11; setup,
planner, IA determinista, Risk Engine, broker, manager, SQLite y proyecciones usan código real.
No es una evaluación de la capacidad predictiva de los scouts ni un backtest de rentabilidad.

Secuencia: dos órdenes → dos fills posteriores → posiciones abiertas → mark-to-market
→ XAUUSD TP / EURUSD SL → WATCH sin nueva orden. Se comprueban todos los eventos del journal
en orden cronológico, incluidos REX, reports durables e idempotencia de cada slot.
Cuenta fixture USD 10 000, cantidades PAPER 10 / 9090, equity por slot
10 000 / 10 000 / 10 095.45 / 10 209.10; PnL realizado final 209.10.
Coste vigente del contrato PAPER: cero; se comprueba la fórmula y ausencia de doble cierre/cobro.
Esto no establece ni activa un nuevo baseline económico.

## Mapping F12 a tests

Los sufijos siguientes corresponden a métodos `test_e2e_` del archivo citado.

| ID | Transición | Test / evidencia |
|---|---|---|
| F12-T01 | DATA → ANALYSIS | `F12_T01_T13_market_to_exit_accounting`: frames 5m/15m/1h cerrados, símbolos correctos y sin lookahead; journal DATA_CHECK/STRUCTURE/LIQUIDITY/MACRO. |
| F12-T02 | ANALYSIS → SETUP | mismo test: VALID_SETUP; ausencia de sweep en salida produce WATCH. |
| F12-T03 | SETUP → PLAN | mismo test + `F12_T14_T16_notifications_review_ui_and_full_journal`: PLAN_CREATED antes de orden. |
| F12-T04 | PLAN → TARGET/R:R | `F12_T01_T13_market_to_exit_accounting`: geometría autorizada 3R, LONG/SHORT y precios de salida SL/TP congelados. |
| F12-T05 | TARGET/R:R → RISK | mismo test: APPROVED, cantidad PAPER esperada, capital en riesgo <=1%. |
| F12-T06 | RISK → AI | mismo test: cinco agentes deterministas OK/PARTIAL, validación ok y referencias de evidencia recibida. |
| F12-T07 | AI → POSITION CONFLICT | mismo test: EXISTING_POSITION en slots con exposición; sin nueva orden. Es el gate operativo existente, no activación del Conflict Engine V2. |
| F12-T08 | POSITION CONFLICT → EXECUTION | mismo test: ORDER_SUBMITTED sólo en primer slot; WATCH bloquea nueva ejecución tras salida. |
| F12-T09 | EXECUTION → ORDER | mismo test: exactamente dos órdenes, cantidades/estados esperados; journal completo en `F12_T14_T16_notifications_review_ui_and_full_journal`. |
| F12-T10 | ORDER → FILL | mismo test: dos fills posteriores a as_of, precio/cantidad/side/order/run enlazados, ninguna repetición por slot. |
| F12-T11 | FILL → POSITION | mismo test: dos posiciones por símbolos habilitados, POSITION_OPENED una vez por símbolo. |
| F12-T12 | POSITION → MANAGEMENT | mismo test: equity/unrealized PnL exactos por slot y fórmula mark-to-market. |
| F12-T13 | MANAGEMENT → EXIT | mismo test: TP LONG / SL SHORT, gross/cost/net y equity/realized PnL, flat final y sin doble contabilización. |
| F12-T14 | EXIT → EMAIL | `F12_T14_T16_notifications_review_ui_and_full_journal`: close vinculado al run de apertura, notificación durable; AlertNotificationSink → GmailEmailChannel con SMTP fake, dos mensajes y ledger DELIVERED. |
| F12-T15 | EMAIL → DASHBOARD | mismo test: snapshot persistido/adaptado y `current_market` read-only reflejan cuenta/órdenes FILLED/flat; delivery y lectura no mutan PAPER/journal. |
| F12-T16 | DASHBOARD → JOURNAL | mismo test: secuencia completa y exacta de todos los eventos, review de apertura actualizado con cierre y proyección final WATCH. |
| F12-T17 | Auditar transiciones y fallos | `F12_T17_transition_failures_do_not_authorize_orders`: datos ausentes, macro caída, contrato sin multiplier y IA caída → razones/eventos explícitos, review durable, ninguna orden ni mutación económica. También F11-T01…T17 cubre fallos por capa. |

Controles transversales:

- `F12_rex_hash_chain_and_independent_reproducibility`: dos DBs nuevas independientes,
  generador de UUIDs deterministas existente de P8 reiniciado por replay; normalización JSON
  canónica que conserva todas las filas, IDs, payloads, digests y timestamps de mercado/runtime.
  Sólo sustituye timestamp de observación REX (reloj propio de observabilidad).
  Comparación byte a byte y SHA-256 idénticos; REX OFF produce el mismo estado económico.
- `replay.rex_chain.verify`: recomputa digests, cadenas EDG/PSH, cobertura de ocho runs y
  diez eventos económicos; oracle G14 PASS por run, source_unchanged.
  Resultado exigido SQLITE_CHECKS_PASS / NOT VERIFIED: no sustituye anchors/atestación externos.
- `F12_nas100_blocked_before_data_or_writes`: configuración NAS100 rechazada y run bloqueado
  antes de consultar datos o escribir; sin snapshot NAS100.

## Límites y gate

Sólo tests y documentación; ninguna dependencia, fix de producción ni expectedFailure nuevo.
Sockets bloqueados en el harness; sin red ni envío real. La prueba de email ejercita la frontera
de entrega sobre notificaciones persistidas con SMTP fake; no certifica el worker asíncrono ni
entrega real de F09-T18. No sustituye revisión visual móvil ni el demo PAPER de Fase 13.
REX ON sólo en configs temporales de prueba; defaults/env de runtime V2 permanecen OFF.

La autorización de issue #30 permite implementar offline; Fase 12 sigue IN PROGRESS.
Certificación requiere gate anterior, revisión independiente y aprobación del owner.
Validación: **5/5 tests focales PASS; 1510 tests completos OK (1 expected failure existente)**,
177.788 s; Ruff y `git diff --check` PASS. Comandos y evidencia: `CHANGELOG_AGENT.md`.


## Certificación ligera (2026-10-11)
- [x] PR #32: E2E determinista (https://github.com/bebote0997/ai-market-system/pull/32), merge 22af2e9, CI https://github.com/bebote0997/ai-market-system/actions/runs/38118073512. Autor Codex; revisión de Grok Bot: APPROVE (2 LOW).
- [x] Los journals son byte-idénticos entre ejecuciones, EDG/REX da SQLITE_CHECKS_PASS y la G14 da PASS.
- Nota (LOW): `replay.status_verifier` devuelve `classification = NOT VERIFIED` **por diseño** (DEC-8.18a): la verificación automática no certifica el estado en vivo, que se hace en la Fase 13. No es un fallo.
- [ ] Diferido a la Fase 13: una replay más larga (varios días) con datos reales grabados.
