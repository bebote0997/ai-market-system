# Trading Floor V2 — Architecture

## Operational layer (Fase 6C)

`runtime.service.OperationalRuntime` coordinates the session scheduler, closed-bar freshness gate, deterministic Floor Orchestrator, AI Orchestrator, final PAPER policy gate, existing Paper Broker and Trade Manager, and SQLite repository. `storage.database.Store` persists reports, paper state, journal, slots, health, and read-only UI snapshots. `runtime.scheduler` uses `zoneinfo` for London/New York analysis windows. The UI reads SQLite read-only and never triggers a cycle. The scheduler triggers but never authorizes. Only `PLAN_READY` with RiskDecision APPROVED and valid review can reach the Paper Broker; AI_CAUTION blocks progression. Details: `OPERATIONAL_RUNTIME_SPEC.md`.

## Diagrama

```mermaid
flowchart TD
    MD[Market Data] --> O[Orchestrator]
    O --> S[Structure Agent]
    O --> L[Liquidity Agent]
    O --> N[Macro/News Agent]
    S --> V[Setup Validator]
    L --> V
    N --> V
    V --> P[Trade Planner]
    P --> RA[Risk Agent]
    RA --> RE[Deterministic Risk Engine]
    RE --> EA[Execution Agent]
    EA --> PB[Paper Broker]
    PB --> TM[Trade Manager]
    TM --> FA[Floor Assistant]
```

## Fronteras

### Market Data

Obtiene y normaliza barras, sesiones, timeframes, símbolos, timezone, fuente y calidad. Debe distinguir vela cerrada de vela en formación. No decide setups ni riesgo.

### Structure Agent

Analiza estructura multi-timeframe: tendencia, soportes, resistencias, desplazamientos y retrocesos. Las definiciones computables deben estar documentadas; las heurísticas deben etiquetarse como tales.

### Liquidity Agent

Detecta zonas, sweeps, order blocks y posibles reacciones según definiciones explícitas. No convierte una heurística en una verdad de mercado.

### Macro/News Agent

Entrega eventos macro y noticias con fuente y timestamp. Si no hay datos, devuelve `NO_DATA`; nunca inventa contexto.

### Setup Validator

Combina evidencia y produce `NO_SETUP`, `WATCH` o `VALID_SETUP`. No calcula sizing ni autoriza riesgo.

### Trade Planner

Convierte un setup válido en una propuesta LONG/SHORT con entrada, invalidación, stop, target, R:R y condiciones de cancelación.

### Risk Agent

Interpreta la propuesta y prepara la solicitud al motor determinista. No es la autoridad matemática final.

### Deterministic Risk Engine

Valida límites, dirección, stop, target, R:R, valor monetario por punto/tick y cantidad. Produce `APPROVED` o `REJECTED`. Ningún agente puede saltárselo.

### Execution Agent / Paper Broker

Ejecuta solo planes autorizados en paper trading. No reinterpreta la estrategia.

### Trade Manager

Mantiene estado, SL/TP, fills, cancelaciones y posiciones. No mueve stops arbitrariamente.

### Floor Assistant

Resume evidencia, estados, riesgo, órdenes paper y decisiones. No toma decisiones financieras.

## Contratos mínimos

Usar dataclasses o TypedDict versionados. Todo mensaje importante debe incluir como mínimo:

```text
schema_version
run_id
timestamp
symbol
timeframe
agent
status
evidence
reasoning_summary
data_quality
warnings
```

Estados técnicos base: `OK`, `PARTIAL`, `NO_DATA`, `ERROR`.

Contratos específicos posteriores:

- `MarketBar`: OHLCV, símbolo, timeframe, timestamp, timezone, fuente, `is_closed`.
- `SetupAssessment`: estado, side, timeframes, evidencia e invalidación.
- `TradePlan`: side, entry, stop, target, R:R, invalidación y cancelación.
- `RiskDecision`: estado, cantidad, capital en riesgo, niveles y motivo.

## Determinismo

Indicadores, estructura formal, niveles, sizing, riesgo, ejecución paper, PnL, equity, drawdown y autorización son deterministas. La IA solo interpreta, clasifica, sintetiza y explica.

## Compatibilidad temporal

Preservar no-lookahead, train/test, contexto histórico de prueba, mark-to-market, posiciones abiertas, costes una sola vez y política conservadora intrabar.

## Capa de agentes IA (Fase 6A)

```mermaid
flowchart TD
    S[Structure Agent] --> SAI[Structure AI]
    L[Liquidity Agent] --> LAI[Liquidity AI]
    N[Macro/News Agent] --> MAI[Macro AI]
    SAI --> SRAI[AI Setup Reviewer]
    LAI --> SRAI
    MAI --> SRAI
    SRAI --> V[Setup Validator]
    V --> P[Trade Planner]
    P --> TRAI[AI Trade Reviewer]
    TRAI --> RE[Deterministic Risk Engine]
```

Regla central: **AI interprets. Python validates. Risk Engine authorizes. Paper Broker executes.**

- `ai/contracts.py`: `AIRequest`/`AIResponse`/`AIFloorReport` versionados y frozen; `validate_ai_response` es la única puerta de entrada de una respuesta IA hacia el resto del sistema.
- `ai/provider.py`: interfaz `AIProvider.generate(request) -> AIResponse`; `DeterministicAIProvider` (por defecto, sin red) y `FakeAIProvider` (pruebas). Un proveedor LLM real puede añadirse implementando la misma interfaz sin tocar Risk Engine, Paper Broker ni scouts deterministas.
- `ai/runtime.py`: invoca al proveedor, valida el grounding, aísla fallos (`ERROR` en vez de excepción propagada), evita llamadas sin evidencia (`NO_DATA`) y registra un `AuditLog` en memoria.
- `ai/agents/`: Structure AI, Liquidity AI, Macro AI, AI Setup Reviewer y AI Trade Reviewer. Ninguno inventa evidencia; cada uno solo cita `evidence_id` recibidos en el `AIRequest`.
- `ai/orchestrator.py`: capa de composición sobre un `FloorRunReport` ya calculado por `floor/orchestrator.py` (sin modificarlo). Nunca convierte `NO_SETUP`/`WATCH`/`RISK_REJECTED` en un estado ejecutable; solo puede añadir `AI_CAUTION` cuando un `PLAN_READY` recibe una revisión IA adversa, sin alterar el `RiskDecision`.

`REAL_EXECUTION` permanece `DISABLED`. El módulo `ai/` no importa `execution.paper_broker` ni `execution.trade_manager`.
