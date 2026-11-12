# Fase 13 — Long Paper Validation

**Estado:** STARTED 2026-10-14 08:29 ET (DEC-13.1, opción B).

Demo iniciado con GO del owner sobre la DB actual (sin DB nueva ni sealed genesis); ver DEC-13.1 y el runbook.

2026-11-07, DEC-EMA-1, solo preparado: el cruce EMA 10/21 puede añadirse al demo con `AI_FLOOR_EMA_CROSS=1` y no está activado. El demo en curso y DEC-FX-SL-3 no cambian. Ver `docs/ema_cross.md`.

2026-11-07, DEC-XAI-1, solo preparado y apilado encima: xAI no cambia el proveedor en marcha. El owner puede pedir el paso desde el panel; el runner lo anota solo con la cuenta plana, y el arranque no lo hace. Ver `docs/provider_migration.md`.

2026-11-12, DEC-EXNESS-USC-1, solo preparado y apilado sobre el feed M5: la cuenta centavo USC usa sus campos vivos. Frenos 1000/5000 USC y tope 500 USC. La ejecución real sigue apagada. Ver `docs/exness_api.md`.

2026-11-11, DEC-EXNESS-M5-1, solo preparado y apilado sobre el session combo: velas M5 desde ticks de Exness, apagadas. `AI_FLOOR_EXNESS_M5_FEED` no arma órdenes reales. Ver `docs/exness_m5.md`.

2026-11-11, DEC-SESSION-COMBO-1, solo preparado y apilado sobre el adaptador Exness: rango asiático USDJPY y fade del fix de Londres. `AI_FLOOR_SESSION_COMBO` apagado. No envía órdenes sin las dos claves reales. Ver `docs/session_combo.md`.

2026-11-11, DEC-EXNESS-1, solo preparado: adaptador de la API oficial de Exness, apagado. No sustituye al demo OANDA. Ver `docs/exness_api.md`.

2026-11-11, DEC-EMA-ONLY-1, solo preparado: `AI_FLOOR_EMA_ONLY` apagado. Con `1` en practice el router no envía el plan del motor y cancela una LIMIT de retroceso sin fill (`EMA_ONLY`). EMA y la gestión no cambian. Sin merge ni deploy.

2026-11-11, DEC-XAI-429-1, solo preparado: `XAI_RATE_LIMIT_V2` apagado. Con `1`, las llamadas a xAI van de una en una y un HTTP 429 espera (backoff y `Retry-After`) dentro de 90 s por llamada y 180 s por ciclo, contando el HTTP; si no cabe otro intento, el ciclo sigue en fallo cerrado. Sin cambio de estrategia, riesgo, órdenes, cadencia ni Render.

PR #39, gold options 1 y 4 autorizadas por el owner (2026-10-13): fix de continuidad macro en refrescos
de 6 h, sin backdating de fetched_at/known_at; auditoría SQLite por ciclo, acotada a 30 días / 10 000 filas,
con gates y estados 1h/15m/5m, y logging INFO a stdout. Alineación y lógica de estrategia intactas.
Fallos de auditoría nunca cambian el ciclo; DUPLICATE no sobrescribe el registro y R-HALT suprime escrituras.
Sin activación ni deploy; validación y limitaciones en CHANGELOG_AGENT.md.

Pre-demo PR #39, review fixes locales (2026-10-13): conservar el bloqueo PAPER de cancelación tras dos CAS
fallidos aunque el catch-up posterior tenga éxito; señal de alertas desde la orden original o su review de envío.
Regresiones de cancelación sin fill y cierre iniciado por otro run; validación en CHANGELOG_AGENT.md.
Sin activación, merge ni deploy; el GO del owner sigue pendiente.

**Tareas (Libro v1.9):** **30 días** (decisión del owner 2026-10-10; el Libro v1.9 decía 60 días con auditorías Day 30 y Day 60 — cambio registrado en `docs/decisions.md`). Auditoría final al Day 30. Incluye la validación en vivo diferida desde Fase 8 (DEC-8.23). Métricas: ciclos, setups, trades, wins/losses, win rate, R:R medio, PnL, drawdown, exposición, fallos API/datos/email, uptime, coste y uso de proveedor, multi-posición, duplicados, recuperación, V1 vs V2, suficiencia de evidencia (F13-T01…T21). No autoriza ejecución real.

**Gate:** requiere fase anterior CERTIFIED y autorización explícita del owner.

DEC-14.2 (owner, 2026-10-11 11:33 ET): el demo habilitará `V2_PLANNER_POLICY=V2_F3` para planificar a 3.00R y aceptar fills PAPER ≥ 2.50R (DEC-4.7). Default V1 hasta la activación operativa autorizada. Independiente de `V2_OPPOSITE_SETUP_POLICY`; no cambia el GO requerido ni activa Render. El oráculo REX existente solo verifica V1: con F3 identifica la política efectiva y reporta `UNSUPPORTED_RR_POLICY`, sin certificar esa ejecución.
## DEC-14.1 — política de setups opuestos

El demo PAPER habilitará `V2_OPPOSITE_SETUP_POLICY=STRUCTURE_CHANGE` (decisión del owner
Jeferson Tejeda, 2026-10-11 10:00 ET). **No está habilitado todavía**: default `BLOCK`, sin
cambio de env, Render, scheduler ni deploy. GO operativo y evidencia/certificación siguen pendientes.
Pre-flight: tests de DEC-14.1, replay con corpus original disponible, warning fail-closed para valores
inválidos y verificación del cierre/reverse/abandono en journal y alertas. Diseño/restart en fase-06.

## 2026-10-17 · Demo de broker OANDA (fuera de las fases certificadas)
Ver `docs/oanda_adapter.md` y `docs/security_audit.md`. Sin cambios en el estado de las fases 0–13.

## 2026-10-30 · DEC-EURUSD-PULLBACK-1 (borrador, sin certificación)

Entrada límite EURUSD solo en el router del demo OANDA practice (`execution/eurusd_pullback.py`,
`execution/broker_demo_router.py`). No cambia el planner, el Risk Engine ni el filtro de noticias.
Flag `AI_FLOOR_EURUSD_LIMIT_PULLBACK`: ON si no está definido; `0` vuelve a la orden de mercado.
Las posiciones ya abiertas no se re-gestionan. Fase 13 sigue STARTED. Sin merge, sin deploy, sin Render.
Detalle en `docs/decisions.md` y `CHANGELOG_AGENT.md`.
