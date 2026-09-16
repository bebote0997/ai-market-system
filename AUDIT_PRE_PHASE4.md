# Pre-Phase 4 Audit

## Baseline

- Commit auditado: `29603d0` (`Crea agente macro y noticias`)
- Tests antes: `253 OK`
- Tests después: `268 OK`
- Resultado: `PASSED`

## Componentes auditados

- `core/`, `data/`, `agents/`
- `riesgo.py`, `simulador.py`, `estrategia.py`
- `validacion_historica.py`, `tecnico.py`, `mercado.py`
- `motor_analisis.py`, `backtest.py`
- `validacion_fuera_muestra.py`, `evaluacion_estrategia.py`
- `ejecutar_estrategia.py`, `investigacion.py`, `app.py`
- Todos los tests existentes y nuevos

## Defectos encontrados y corregidos

### HIGH — BOS dependía del orden de búsqueda

`Structure Agent` buscaba primero rupturas bullish y podía devolver una ruptura antigua aunque existiera una bearish posterior.

Corrección: se recopilan BOS válidos y se devuelve el último por `break_timestamp`.

### HIGH — Liquidity Agent usaba barras adyacentes como equal highs/lows

Eso podía clasificar liquidez sobre cualquier par de barras, no sobre niveles estructurales.

Corrección: equal highs/lows se calculan sobre pivotes confirmados. La regla sigue marcada como `HEURISTIC` y usa tolerancia relativa explícita del 0.1%.

### HIGH — Macro/News no distinguía correctamente conocimiento y resultado

`received_at=None` podía permitir que un `actual` futuro pareciera disponible.

Corrección: contratos con `known_at` y `result_timestamp`; actuals sin timestamp verificable de resultado se rechazan para contexto histórico. News usa `known_at` derivado de recepción/publicación cuando es verificable.

### MEDIUM — Deduplicación Macro/News sin namespace de fuente

IDs iguales de proveedores distintos podían colisionar.

Corrección: las claves de deduplicación incluyen `source`.

### LOW — Warning de pandas por formato temporal ambiguo

Corrección: `format="mixed"` en la normalización temporal. No se desactivaron warnings.

## Tests de regresión añadidos

- BOS bullish/bearish y selección temporal entre ambos.
- Swing no confirmado y cierre sin ruptura.
- Retracement bullish/bearish/ausente y no-lookahead.
- Equal highs/lows sobre pivotes confirmados.
- Deduplicación por proveedor.
- `known_at`, actual futuro y timestamps inválidos.

## Checks

- `no_lookahead_check`: PASSED; Structure/Liquidity aceptan `as_of` y tests demuestran estabilidad histórica.
- `risk_check`: PASSED por la suite existente; no se cambiaron límites ni semántica económica.
- `simulator_check`: PASSED por la suite existente; LONG/SHORT, gaps, costes, equity y posiciones abiertas conservados.
- `macro_news_check`: PASSED; fuente/timestamp, `known_at`, freshness y deduplicación cubiertos.
- `structure_check`: PASSED; BOS y retracement formalizados y testeados.
- `liquidity_check`: PASSED; pivotes, pools y sweeps testeados.
- `security_check`: no se encontraron API keys, passwords, tokens ni claves privadas en Python; no existe broker real.

## Deuda no bloqueante

- Dataclasses frozen contienen algunos `dict`/`tuple` que no son profundamente inmutables; cambiarlo ahora podría romper contratos existentes.
- `InstrumentSpec` mantiene multiplicadores desconocidos como `None` para evitar inventar especificaciones.
- `yfinance` sigue siendo proveedor limitado para intradía y no se convirtió en proveedor V2 durante esta auditoría.
- Warnings de `ScriptRunContext` de Streamlit permanecen como ruido legítimo de tests.

## Estado final

- Fases 0-3: COMPLETADAS.
- Pre-Phase-4 Audit: PASSED.
- Fase 4: NEXT.
- No se implementaron Setup Validator, Trade Planner, Orchestrator, broker ni UI nueva.
