# AI Market System — Agent Protocol

## Fuente de verdad

Antes de modificar código, todo agente debe leer, en este orden:

1. `AGENTS.md`
2. `TRADING_FLOOR_SPEC.md`
3. `ARCHITECTURE.md`
4. `ROADMAP.md`

Después debe:

5. Ejecutar la suite baseline.
6. Inspeccionar el código existente antes de crear duplicados.
7. Implementar únicamente la fase o tarea autorizada.
8. Ejecutar la suite completa al terminar.
9. Actualizar `CHANGELOG_AGENT.md`.
10. No hacer `git add`, commit ni push salvo autorización explícita.

## Reglas de trabajo

- No modificar archivos fuera del alcance autorizado.
- No introducir dependencias sin autorización.
- Preservar compatibilidad, no-lookahead, train/test, equity mark-to-market, posiciones abiertas y costes cobrados una sola vez.
- Preferir cambios pequeños, deterministas y testeables.
- No ocultar errores de contrato con accesos defensivos que borren la causa raíz.
- No inventar datos ausentes; usar estados explícitos como `NO_DATA`.

## Producto congelado

AI Market System evolucionará hacia un AI Trading Floor personal especializado inicialmente en:

- `XAUUSD`
- `NAS100` / `US100`
- `EURUSD`

Timeframes:

- `1H`: contexto principal
- `15M`: estructura y setup
- `5M`: ejecución y refinamiento

Sesiones prioritarias: Londres y Nueva York.

Características operativas:

- Intradía y scalping selectivo.
- LONG y SHORT.
- Sin obligación de operar.
- Máximo 1% de riesgo por operación.
- R:R mínimo base 1:3.
- Paper trading únicamente.
- Ningún broker real ni dinero real.

Estados de setup:

- `NO_SETUP`
- `WATCH`
- `VALID_SETUP`

## Separación de responsabilidades

La IA puede interpretar, clasificar, sintetizar y explicar evidencia.

Python determinista controla datos numéricos, risk sizing, SL/TP, R:R, límites, estado de órdenes, PnL, equity y autorización final de riesgo.

Ningún agente puede saltarse el Risk Engine. Los agentes se comunican mediante contratos estructurados y versionados, nunca mediante prosa libre como API interna.

Nunca inventar precios, noticias, timestamps, especificaciones contractuales, valores por punto/tick ni datos ausentes. `NO_DATA` es un estado válido.

## Fases

El trabajo debe seguir `ROADMAP.md`. La Fase 0 está completada y la Fase 1 es la siguiente autorizada. No adelantar fases sin autorización explícita.
