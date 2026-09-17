# Auditoría Fase 7 — Demo Runner PAPER

Estado: **PASS** en certificación local el 17 de septiembre de 2026. Base aceptada: `bb20b0d9f79419e351370d7fb8cb037ace19b930`.

## Implementación

- Runner por slot y símbolo con `run_id` durable único, revisión estructurada, journal y notificaciones persistidas antes de entrega.
- Doctor local READY/NOT_READY; modo diagnóstico sin órdenes; UI operacional de solo lectura.
- SQLite schema 2 con migración del schema 1 y recuperación de ciclos interrumpidos.
- Twelve Data activo, OpenAI `gpt-5.6-terra`; Massive soportado/no activo, NAS100 soportado/no habilitado. PAPER únicamente.

## Evidencia offline

Suite completa: **447 tests OK** el 17 de septiembre de 2026. Doce tests nuevos de Demo Runner cubren LONG/SHORT, riesgo aprobado/rechazado, AI_CAUTION, barras stale/future/forming, fallos Twelve Data 429/503, fallo IA, slot duplicado, preflight, migración, notificaciones, diagnóstico sin órdenes, reinicio con posición abierta y cierre sin doble contabilización. Los tests existentes cubren además invariantes de Risk Engine y Paper Broker, no-lookahead, persistencia y proveedores.

## Diagnóstico live controlado

- Preflight READY; `OPENAI_API_KEY` y `TWELVE_DATA_API_KEY` presentes en el `.env.local` del checkout principal sin mostrar valores. Modelo configurado `gpt-5.6-terra`. Scheduler deshabilitado, PAPER únicamente, ejecución real deshabilitada.
- Primera pasada en sandbox: error de red; no hubo barras ni órdenes. Repetición fuera del sandbox: Twelve Data respondió, pero OpenAI produjo `invalid_recommendation`/`ungrounded_supporting_evidence`. Se restringió el JSON Schema a recomendaciones permitidas e IDs de evidencia de la solicitud; la validación posterior permanece intacta.
- Siguiente pasada: respuestas Structure/Liquidity/Setup válidas; el certificador aún exigía `OK` a Macro sin noticias y a Trade Reviewer sin plan. Se corrigió el criterio y se añadió prueba offline. No se aceptan fallos reales de IA ni datos stale.
- Pasada final **PASS** a `2026-09-17T21:32:25.966358+00:00` para slot UTC `21:30`, sin órdenes. XAUUSD y EURUSD terminaron `WATCH`, con run_id y review/journal durables; OpenAI `READY` y uso positivo (última llamada 1010 y 1030 tokens respectivamente). Structure, Liquidity y Setup Reviewer `OK`; Macro `NO_DATA` por ausencia de fuente, Trade Reviewer no invocado sin plan. Twelve Data METAL/FOREX `READY`; NAS100 y Massive no se consultaron.
- Últimas barras de ambos símbolos: 5m `21:25 UTC` (edad al cierre 462.1 s XAUUSD, 478.7 s EURUSD; límite 600 s); 15m `21:15 UTC` (1062.1/1078.7 s; límite 1800 s); 1h `20:00 UTC` (5562.1/5578.7 s; límite 7200 s). Se comprobó frescura contra reloj real, barras cerradas y el gate del runtime. Cero órdenes PAPER.

## Cierre

Suite final: 447 tests OK. `git diff --check` sin errores; secret scan de archivos de código/configuración/documentación con cero coincidencias de patrones de claves; `.env.local` ignorado por Git y no staged. La referencia remota `origin/main` seguía en la base aceptada al verificar antes del commit. No iniciar 14 días ni scheduler 24/7.
