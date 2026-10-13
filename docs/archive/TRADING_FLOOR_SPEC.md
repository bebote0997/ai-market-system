# Trading Floor V2 — Functional Specification

## Propósito

Construir un Trading Floor personal, auditable y especializado para estudiar y ejecutar paper trading selectivo en `XAUUSD`, `NAS100/US100` y `EURUSD`.

El sistema no debe operar por obligación. La ausencia de setup es un resultado válido.

## Flujo funcional

```text
Market Data
  -> Orchestrator
  -> Structure Agent
  -> Liquidity Agent
  -> Macro/News Agent
  -> Setup Validator
  -> Trade Planner
  -> Risk Agent
  -> Deterministic Risk Engine
  -> Execution Agent
  -> Paper Broker
  -> Trade Manager
  -> Floor Assistant
```

## Timeframes y sesiones

- `1H`: contexto y dirección general.
- `15M`: estructura y setup.
- `5M`: ejecución y refinamiento.
- Sesiones prioritarias: Londres y Nueva York.

Los timestamps deben ser explícitos, normalizados y acompañados por fuente y calidad de datos.

## Reglas congeladas

- LONG y SHORT.
- Riesgo máximo: 1% por operación.
- R:R mínimo base: 1:3.
- Paper trading solamente.
- Sin broker real, órdenes reales ni dinero real.
- Sin optimización automática, scoring o selección de parámetros basada en test.

## Estados de setup

El Setup Validator devuelve únicamente:

- `NO_SETUP`: evidencia insuficiente o contradictoria.
- `WATCH`: contexto interesante, pero falta confirmación.
- `VALID_SETUP`: evidencia suficiente según reglas explícitas.

Ningún estado implica por sí mismo una orden. Toda propuesta debe pasar por el Risk Engine.

## Datos ausentes

`NO_DATA` es válido. Ningún componente puede inventar precios, noticias, timestamps, sesiones, especificaciones de contrato o datos por punto/tick.

## Evidencia y explicabilidad

Cada decisión importante debe poder reconstruirse con:

- símbolo
- timeframe
- timestamp
- agente
- estado
- evidencia
- resumen de razonamiento
- calidad de datos
- warnings

La IA explica y sintetiza. No modifica cálculos, sizing, niveles, límites ni autorización final.

## Capa IA (Fase 6A)

AI interprets. Python validates. Risk Engine authorizes. Paper Broker executes.

Ningún proveedor IA puede: inventar precios, modificar OHLC, modificar equity, modificar la cantidad aprobada, saltarse el Risk Engine, aprobar más de 1% de riesgo, bajar el R:R mínimo, ejecutar órdenes directamente, modificar `PaperAccount`/`PaperPosition`, ni acceder a un broker real.

Toda respuesta IA es un `AIResponse` estructurado (ver `AI_RUNTIME_SPEC.md`), validado contra el `AIRequest` que la originó antes de ser usada por cualquier otro componente. Una respuesta que cite evidencia no suministrada, cambie de símbolo/run_id/timestamp o exceda los rangos permitidos se rechaza y se reemplaza por un estado `ERROR`, sin detener el resto del run determinista.
