# Certificación de proveedores — Fase 6D

Estado: **PASS** en la certificación live controlada del 17 de septiembre de 2026, 17:54:42 UTC. El experimento PAPER de 14 días no ha comenzado.

La orden activa es OpenAI (una llamada) y Twelve Data (XAUUSD `XAU/USD`, EURUSD `EUR/USD`, 5m/15m/1h). Ejecutar desde la raíz con `python -m runtime.certify_providers --json --env-file <ruta-local>` únicamente cuando las credenciales locales estén disponibles. El comando no crea órdenes ni llama a Massive. Massive se registra `SUPPORTED / NOT_ACTIVE`; NAS100/I:NDX, `SUPPORTED / NOT_ENABLED`.

PASS requiere contrato OpenAI válido con uso registrado, ticker e intervalo exactos de Twelve Data, barras cerradas UTC y frescura dentro de los límites existentes. `STALE_DATA`, `EMPTY`, `AUTH_ERROR`, `ENTITLEMENT_ERROR`, `RATE_LIMITED`, `PROVIDER_FAILURE` y estado de mercado incierto con barras antiguas permanecen bloqueantes. Ningún resultado parcial autoriza el cierre ni el inicio del experimento.

## Resultado live

- OpenAI `gpt-5.6-terra`: **PASS**. Responses devolvió `AIResponse` estructurado con `response_status=NO_DATA`, válido para la evidencia mínima de certificación; identidad y grounding `contract=ok`; usage: 599 tokens de entrada, 241 de salida y 840 totales. Una sola llamada en esta pasada; sin sustitución de modelo.
- Twelve Data: **PASS** para XAUUSD `XAU/USD` y EURUSD `EUR/USD`. `meta.symbol` y `meta.interval` coincidieron con cada solicitud; se descartaron barras en formación. Hora de certificación: `2026-09-17T17:54:42.306837+00:00`.

| Símbolo | Intervalo | Última barra UTC (inicio) | Edad (s) | Umbral (s) | Estado |
|---|---|---|---:|---:|---|
| XAUUSD | 5m | 2026-09-17 17:45 | 582.307 | 600 | PASS |
| XAUUSD | 15m | 2026-09-17 17:30 | 1482.307 | 1800 | PASS |
| XAUUSD | 1h | 2026-09-17 16:00 | 6882.307 | 7200 | PASS |
| EURUSD | 5m | 2026-09-17 17:45 | 582.307 | 600 | PASS |
| EURUSD | 15m | 2026-09-17 17:30 | 1482.307 | 1800 | PASS |
| EURUSD | 1h | 2026-09-17 16:00 | 6882.307 | 7200 | PASS |

No se solicitó un endpoint adicional para estado de mercado: figura `UNKNOWN`; las seis series tenían barras cerradas dentro de los umbrales. Massive: `SUPPORTED / NOT_ACTIVE`, sin consultas. NAS100/I:NDX: `SUPPORTED / NOT_ENABLED`, sin consultas. Scheduler deshabilitado, PAPER únicamente, ejecución real deshabilitada.
