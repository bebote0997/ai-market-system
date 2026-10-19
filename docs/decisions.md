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
| DEC-14.2 | 2026-10-11 11:33 ET | **APPROVED** (owner Jeferson Tejeda) | Runtime opt-in via `V2_PLANNER_POLICY=V2_F3`: planned 3.00R, PAPER fill floor 2.50R (DEC-4.7), SL/TP inmutables. Spread, latencia y movimiento del precio entre planificación y ejecución requieren esa tolerancia. Default `V1` hasta habilitar el flag en el demo; valores desconocidos → V1 + warning. Independiente de `V2_OPPOSITE_SETUP_POLICY`; Policy D sigue prohibida en runtime. |
| DEC-4.7 | 2026-10-06 | APPROVED | Piso de fill PAPER 2.50R; SL/TP inmutables |
| DEC-5.5 | 2026-10-06 | APPROVED | Equity ≤ 95 % del inicial → BLOCK NEW RISK |
| **DEC-5.5a** | **2026-10-17** | **APPROVED** (owner Jeferson Tejeda) | Enmienda de DEC-5.5: **límite de pérdida diaria = 2 % del equity por día** (base NAV incl. P&L abierto, día 17:00 America/New_York, DAILY_HALT: entradas nuevas bloqueadas **y flatten ON** (cierra posiciones abiertas; aprobado 2026-10-17 t290u), reanuda al día siguiente; el gate de drawdown 5 % se mantiene). Sustituye «daily_loss_limit: none authorized». **Solo decisión: sin implementar** (borrador + tests pendientes; REAL sigue bloqueado). Detalle en `real_trading_readiness_plan.md` §4.1 |
| **DEC-LIVE-1** | **2026-10-17** | **APPROVED** (owner Jeferson Tejeda) | Umbrales G1–G11 del demo OANDA→real aprobados tal como se propusieron; capital live previsto $1 000 tras superar el demo de 30 días. Sin cambios de código ni de Render; el trading real sigue bloqueado |
| **DEC-DEMO-GATE-1** | **2026-10-19 10:29 ET** | **APPROVED** (owner Jeferson Tejeda) | Con el enrutado demo OANDA ON (`AI_FLOOR_BROKER_DEMO_ROUTING=1`) el gate PAPER de drawdown 5 % (Risk V2, gate 4, DEC-5.5) usa **solo** el NAV de la cuenta OANDA practice frente a la base del demo **99 999,98** (NAV en el inicio 2026-10-18 18:25 ET; override `AI_FLOOR_DEMO_START_EQUITY`, no fijado en producción); NAV ilegible/inválido ⇒ fail-closed `ACCOUNT_DRAWDOWN_UNAVAILABLE` (solo riesgo nuevo). Sizing y demás gates siguen con la cuenta PAPER; mismos valores de política. El resumen diario de Slack reporta cifras del demo OANDA (sin datos ⇒ «OANDA demo: sin datos», nunca PAPER). Los datos PAPER quedan archivados y no se usan para cifras del demo. Enrutado OFF ⇒ sin cambios. Branch `feat/demo-gates-oanda` |
| DEC-5.7 | 2026-10-06 | APPROVED | Fill-money risk ≤ riesgo monetario planificado × 8/7 (política registrada) |
| DEC-6.2 A | 2026-10-07 | APPROVED | Sin SAME_THESIS / NEW_STRUCTURE / TREND_CHANGE (T02/T04/T05 N/A) |
| DEC-6.3/6.4 A | 2026-10-07 | APPROVED | Misma dirección → BLOCK; sin pyramiding; una exposición por símbolo |
| DEC-8.13…8.16, 8.21 | 2026-10-08 | APPROVED POLICY | Ver `archive/V2_PHASE8_P86_DESIGN.md` §8.4 |
| DEC-8.17 / 8.17b | 2026-10-08 | APPROVED POLICY | Medición G-8.INT; política de halt (Alt. 1) |
| DEC-8.20 | 2026-10-08 | APPROVED POLICY | A2: nueva cuenta PAPER USD 10 000, DB nueva, sin posiciones heredadas, sealed genesis |
| DEC-8.21b | 2026-10-08 | PROVISIONAL | B-STRICT pendiente de validación operativa |
| HIGH-8.1 owner decision | 2026-10-10 | RECORDED | Ruta OFF EURUSD/XAUUSD → catch-up cronológico; PnL no comparable con baseline congelado (`archive/HIGH81_OWNER_DECISION.md`) |
| **DEC-8.18** | 2026-10-10 | **APPROVED** (owner Jeferson Tejeda / bebote0997) | Status-verification procedure (axes, H5), aprobado tal cual |
| **DEC-8.19** | 2026-10-10 | **APPROVED** (owner Jeferson Tejeda / bebote0997) | Boundary handling = opción (a): precondición flat bajo A2 (sin posiciones ni órdenes heredadas) |
| **DEC-8.23** | 2026-10-10 | **APPROVED** (owner) | Gates G-8.INT de sesión en vivo (G2/G3/G4/G5/G7/G11) se certifican con evidencia offline/replay (`replay.g8int`, fixtures deterministas, CI en el SHA exacto); validación en vivo pasa al demo de 30 días (= Fase 13), que arranca tras certificar las Fases 8–12 |
| **DEC-8.18a** | 2026-10-10 | **APPROVED** (Grok Bot, control delegado por el owner) | Enmienda de DEC-8.18: sin firma SSH H5; Q1–Q8 como script automático `replay/status_verifier.py` (PR #18); verificación en vivo en Fase 13 |
| **DEC-8.24** | 2026-10-10 | **APPROVED** (Grok Bot, bajo el control total otorgado por el owner 2026-10-10 22:01 ET; se informa al owner) | Criterio de certificación de Fase 8: filas offline PASS y sin FAIL; G8 (gaps de evidencia sin explicar) y G15 (cadena EDG con evidencia externa) se difieren a Fase 13 junto con DEC-8.23 |
| DEC-13.0 (owner) | 2026-10-10 | **APPROVED** (owner) | Fase 13 = demo PAPER de 30 días (sustituye los 60 días del Libro v1.9) y se lanza solo tras certificar Fases 8–12 |
| **DEC-13.1** | **2026-10-14 08:29 ET** | **APPROVED** (owner Jeferson Tejeda) | GO de la Fase 13, opción B: día 1/30 en la DB actual del trial (`data/runtime/trial/trading_floor.db`), sin DB nueva ni reinicio; incluye la posición XAUUSD SHORT abierta (entrada 4139.45, SL 4216.21, TP 3907.85) y el +USD 9.32 realizado (equity 10 009.32). Desviación de DEC-8.20/DEC-8.19 (DB nueva, flat, sealed genesis) aceptada por el owner. `AI_FLOOR_V2_POSITION_CATCH_UP=0` se mantiene; `AI_FLOOR_MACRO_PROVIDER=fxmacrodata` sin cambios. Solo PAPER, `REAL_EXECUTION_ENABLED=False`, NAS100 bloqueado. |
| **DEC-14.1** | **2026-10-11 10:00 ET** | **APPROVED — owner Jeferson Tejeda** | Política runtime `V2_OPPOSITE_SETUP_POLICY=BLOCK` (default, legado sin cambios) o `STRUCTURE_CHANGE`: supersede DEC-6.5 A / DEC-6.6 A solo con flag ON; BOS contrario cerrado más allá del SL, datos frescos y setup validado; beneficio → cierre PAPER y nuevo Risk V2; pérdida → conservar si estructura 1H intacta, si rota cerrar y reevaluar Risk V2. Sin pyramiding ni cancelación de pendientes; IA solo puede bloquear. Demo habilitará el flag, aún no activado. |
| DEC-8.22-a…j, OP-1…7 | — | NOT AUTHORIZED | Autorizaciones de implementación/operación |
