# Fase 13 — Long Paper Validation

**Estado:** NOT STARTED.

**Tareas (Libro v1.9):** **30 días** (decisión del owner 2026-10-10; el Libro v1.9 decía 60 días con auditorías Day 30 y Day 60 — cambio registrado en `docs/decisions.md`). Auditoría final al Day 30. Incluye la validación en vivo diferida desde Fase 8 (DEC-8.23). Métricas: ciclos, setups, trades, wins/losses, win rate, R:R medio, PnL, drawdown, exposición, fallos API/datos/email, uptime, coste y uso de proveedor, multi-posición, duplicados, recuperación, V1 vs V2, suficiencia de evidencia (F13-T01…T21). No autoriza ejecución real.

**Gate:** requiere fase anterior CERTIFIED y autorización explícita del owner.

DEC-14.2 (owner, 2026-10-11 11:33 ET): el demo habilitará `V2_PLANNER_POLICY=V2_F3` para planificar a 3.00R y aceptar fills PAPER ≥ 2.50R (DEC-4.7). Default V1 hasta la activación operativa autorizada. Independiente de `V2_OPPOSITE_SETUP_POLICY`; no cambia el GO requerido ni activa Render. El oráculo REX existente solo verifica V1: con F3 identifica la política efectiva y reporta `UNSUPPORTED_RR_POLICY`, sin certificar esa ejecución.
## DEC-14.1 — política de setups opuestos

El demo PAPER habilitará `V2_OPPOSITE_SETUP_POLICY=STRUCTURE_CHANGE` (decisión del owner
Jeferson Tejeda, 2026-10-11 10:00 ET). **No está habilitado todavía**: default `BLOCK`, sin
cambio de env, Render, scheduler ni deploy. GO operativo y evidencia/certificación siguen pendientes.
Pre-flight: tests de DEC-14.1, replay con corpus original disponible, warning fail-closed para valores
inválidos y verificación del cierre/reverse/abandono en journal y alertas. Diseño/restart en fase-06.
