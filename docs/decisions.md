# Decisiones

Registro resumido. Fuente: Libro Maestro v1.9 (histórico) y documentos de fase del repo. Nuevas decisiones: añadir fila con ID, fecha, estado y enlace a issue/PR.

| ID | Fecha | Estado | Resumen |
|---|---|---|---|
| DEC-006 | 2026-10-02 | APPROVED | Fase 0 CERTIFIED |
| DEC-007 | 2026-10-02 | APPROVED | Requisitos V2 conservados; inicio Fase 1 pendiente |
| DEC-008…012 | 2026-10-05 | APPROVED | Cierre Fase 1; sidecar; transferencias; activación SYSTEM HEALTH bloqueada (DEC-011) |
| DEC-015 | 2026-10-05 | APPROVED | B2.3 runtime activation BLOCKED pendiente autorización + certificación |
| DEC-019 | 2026-10-05 | APPROVED | Carry-forward items OPEN where applicable |
| DEC-4.6 | 2026-10-06 | APPROVED | Planned R:R fijo 3.00R |
| DEC-4.7 | 2026-10-06 | APPROVED | Piso de fill PAPER 2.50R; SL/TP inmutables |
| DEC-5.5 | 2026-10-06 | APPROVED | Equity ≤ 95 % del inicial → BLOCK NEW RISK |
| DEC-5.7 | 2026-10-06 | APPROVED | Fill-money risk ≤ riesgo monetario planificado × 8/7 (política registrada) |
| DEC-6.2 A | 2026-10-07 | APPROVED | Sin SAME_THESIS / NEW_STRUCTURE / TREND_CHANGE (T02/T04/T05 N/A) |
| DEC-6.3/6.4 A | 2026-10-07 | APPROVED | Misma dirección → BLOCK; sin pyramiding; una exposición por símbolo |
| DEC-8.13…8.16, 8.21 | 2026-10-08 | APPROVED POLICY | Ver `V2_PHASE8_P86_DESIGN.md` §8.4 |
| DEC-8.17 / 8.17b | 2026-10-08 | APPROVED POLICY | Medición G-8.INT; política de halt (Alt. 1) |
| DEC-8.20 | 2026-10-08 | APPROVED POLICY | A2: nueva cuenta PAPER USD 10 000, DB nueva, sin posiciones heredadas, sealed genesis |
| DEC-8.21b | 2026-10-08 | PROVISIONAL | B-STRICT pendiente de validación operativa |
| HIGH-8.1 owner decision | 2026-10-10 | RECORDED | Ruta OFF EURUSD/XAUUSD → catch-up cronológico; PnL no comparable con baseline congelado (`HIGH81_OWNER_DECISION.md`) |
| **DEC-8.18** | 2026-10-10 | **APPROVED** (owner Jeferson Tejeda / bebote0997) | Status-verification procedure (axes, H5), aprobado tal cual |
| **DEC-8.19** | 2026-10-10 | **APPROVED** (owner Jeferson Tejeda / bebote0997) | Boundary handling = opción (a): precondición flat bajo A2 (sin posiciones ni órdenes heredadas) |
| **DEC-8.23** | 2026-10-10 | **APPROVED** (owner) | Gates G-8.INT de sesión en vivo (G2/G3/G4/G5/G7/G11) se certifican con evidencia offline/replay (`replay.g8int`, fixtures deterministas, CI en el SHA exacto); validación en vivo pasa al demo de 30 días (= Fase 13), que arranca tras certificar las Fases 8–12 |
| **DEC-8.18a** | 2026-10-10 | **APPROVED** (Grok Bot, control delegado por el owner) | Enmienda de DEC-8.18: sin firma SSH H5; Q1–Q8 como script automático `replay/status_verifier.py` (PR #18); verificación en vivo en Fase 13 |
| **DEC-8.24** | 2026-10-10 | **APPROVED** (Grok Bot, bajo el control total otorgado por el owner 2026-10-10 22:01 ET; se informa al owner) | Criterio de certificación de Fase 8: filas offline PASS y sin FAIL; G8 (gaps de evidencia sin explicar) y G15 (cadena EDG con evidencia externa) se difieren a Fase 13 junto con DEC-8.23 |
| DEC-13.0 (owner) | 2026-10-10 | **APPROVED** (owner) | Fase 13 = demo PAPER de 30 días (sustituye los 60 días del Libro v1.9) y se lanza solo tras certificar Fases 8–12 |
| **DEC-14.1** | **2026-10-11 10:00 ET** | **APPROVED — owner Jeferson Tejeda** | Política runtime `V2_OPPOSITE_SETUP_POLICY=BLOCK` (default, legado sin cambios) o `STRUCTURE_CHANGE`: supersede DEC-6.5 A / DEC-6.6 A solo con flag ON; BOS contrario cerrado más allá del SL, datos frescos y setup validado; beneficio → cierre PAPER y nuevo Risk V2; pérdida → conservar si estructura 1H intacta, si rota cerrar y reevaluar Risk V2. Sin pyramiding ni cancelación de pendientes; IA solo puede bloquear. Demo habilitará el flag, aún no activado. |
| DEC-8.22-a…j, OP-1…7 | — | NOT AUTHORIZED | Autorizaciones de implementación/operación |
