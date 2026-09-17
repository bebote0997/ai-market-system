# Integración de proveedores — Fase 6D

## Selección operativa

`AI_FLOOR_MARKET_PROVIDER=twelve_data` selecciona el adapter activo de mercado para la demo PAPER. `massive` permanece implementado y configurable, pero está NOT_ACTIVE en la certificación de esta demo. `SUPPORTED_SYMBOLS` contiene XAUUSD, NAS100 y EURUSD; `enabled_symbols` contiene solo XAUUSD y EURUSD. NAS100 conserva `I:NDX` en Massive, figura NOT_ENABLED y no se consulta. Ningún adapter autoriza ejecución real ni altera la estrategia o el Risk Engine.

## Twelve Data

Mapeo oficial: XAUUSD → `XAU/USD` (Gold Spot) y EURUSD → `EUR/USD`. La fuente es `/time_series` de Twelve Data. Se solicitan `5min`, `15min` y `1h` con `timezone=UTC`, `format=JSON` y un historial acotado. Cada respuesta debe declarar ticker e intervalo exactos. La API entrega `datetime` de apertura de la barra; el adapter descarta cualquier barra cuya ventana no haya cerrado en `as_of`. Normaliza Open/High/Low/Close, `symbol`, `timeframe`, `provider`, `provider_symbol`, `volume` solo si viene en la respuesta e `is_closed=True` en frames con índice UTC. Rechaza OHLC no finito, no positivo, incoherente o duplicado. No remuestrea, rellena, inventa volumen ni usa datos futuros.

El adapter guarda en memoria únicamente snapshots con la última barra cerrada esperada para cada símbolo/intervalo. Reutiliza la misma respuesta de 1h en las ejecuciones de 15 minutos hasta el siguiente cierre horario y devuelve copias para que un consumidor no modifique la caché. Un resultado stale no se guarda en caché. Cada consulta `/time_series` cuesta un crédito por símbolo según la documentación oficial. El scheduler permanece apagado por defecto y no inicia el experimento.

Las credenciales se leen exclusivamente de entorno o `.env.local` ignorado. Nunca se registran URL completas, cabeceras, cuerpos de error ni valores de claves. HTTP/body 401, 403, 404, 429 y 5xx se clasifican en `AUTH_ERROR`, `ENTITLEMENT_ERROR`, `SYMBOL_UNAVAILABLE`, `RATE_LIMITED` y `PROVIDER_FAILURE`. `Retry-After` se respeta hasta 15 segundos; los demás reintentos son acotados con backoff y jitter. Si falla cualquier intervalo, el runtime devuelve `NO_DATA` y no progresa a una orden PAPER. El freshness gate existente conserva límites de 600 s (5m), 1800 s (15m) y 7200 s (1h).

## OpenAI y certificación

OpenAI sigue siendo advisory. El certificador hace una sola llamada mínima a Responses, valida `AIResponse`, identidad, grounding y usage, y acepta estados contractuales `OK`, `PARTIAL` o `NO_DATA`; `ERROR` falla. La certificación activa de mercado pide exactamente seis series Twelve Data (dos símbolos por tres intervalos), registra última barra UTC, hora UTC de certificación, edad y umbral. Massive NOT_ACTIVE y NAS100 NOT_ENABLED se informan sin consulta. Cualquier stale, empty, entitlement, rate limit o fallo del proveedor impide PASS. Véase `PROVIDER_CERTIFICATION.md`.

Fuentes oficiales: [Twelve Data API docs](https://twelvedata.com/docs), [catálogo Gold Spot `XAU/USD`](https://twelvedata.com/markets/300755/commodity/xau-usd), [símbolos forex](https://support.twelvedata.com/en/articles/5620513-how-to-find-all-available-symbols-at-twelve-data), [créditos API](https://support.twelvedata.com/en/articles/5615854-credits).
