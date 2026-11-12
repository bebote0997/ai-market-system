# Feed M5 de Exness — preparado, apagado

Velas de 5 minutos para el session combo. No arranca salvo que `AI_FLOOR_EXNESS_M5_FEED` valga exactamente `1` y `AI_FLOOR_BROKER` valga exactamente `exness`. Esa bandera no envía órdenes: siguen haciendo falta `AI_FLOOR_SESSION_COMBO=1` y las dos claves de ejecución real.

## Qué hace

- Cada símbolo (USDJPY, GBPUSD, EURUSD) tiene su propio bid/ask. Un tick de uno no refresca al otro. Si el socket cae, todos quedan viejos al momento y no hay orden nueva.
- La vela es OHLC del bid, abierta en una frontera de 5 minutos UTC. Se cierra cuando llega la frontera siguiente o cuando el reloj la cruza. Así la vela de las 01:55 Nueva York está cerrada en la decisión de las 02:00, y las de las 15:45 y 15:55 Londres están cerradas para el fix.
- Si falta una frontera, se anota `M5_GAP` y no se inventa el precio. El combo, si le falta la vela del rango, espera hasta las 02:00 y luego salta el día.
- Al reconectar se pide `GET /v1/market-data/accounts/{id}/candles` con `instrument`, `timeframe=M5`, `price_type=bid`, `from` y `to`. La vela es `{time, open, high, low, close, volume}`.

## Lo que sigue abierto

- El tick confirmado es `{instrument, bid, ask, timestamp}`. Un frame con otra forma se ignora.
- No hay librería WebSocket en `requirements.txt`. El alta, el subscribe, el silencio de 15 s y el reintento están probados con un socket inyectado. El proceso no abre un socket real.

Logs: `M5_FEED_ON` / `M5_FEED_OFF`, `M5_BAR`, `M5_GAP`, `STREAM_SUBSCRIBE`, `STREAM_DROP`. Sin secretos ni id de cuenta.
