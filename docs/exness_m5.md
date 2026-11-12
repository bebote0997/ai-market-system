# Feed M5 de Exness — preparado, apagado

Velas de 5 minutos para el session combo. No arranca salvo que `AI_FLOOR_EXNESS_M5_FEED` valga exactamente `1` y `AI_FLOOR_BROKER` valga exactamente `exness`. Esa bandera no envía órdenes: siguen haciendo falta `AI_FLOOR_SESSION_COMBO=1` y las dos claves de ejecución real.

## Qué hace

- Cada símbolo (USDJPY, GBPUSD, EURUSD) tiene su propio bid/ask. Un tick de uno no refresca al otro. Si el socket cae, todos quedan viejos al momento y no hay orden nueva.
- La vela es OHLC del bid, abierta en una frontera de 5 minutos UTC. Se cierra cuando llega la frontera siguiente o cuando el reloj la cruza. Así la vela de las 01:55 Nueva York está cerrada en la decisión de las 02:00, y las de las 15:45 y 15:55 Londres están cerradas para el fix.
- Si falta una frontera, se anota `M5_GAP` y no se inventa el precio. El combo, si le falta la vela del rango, espera hasta las 02:00 y luego salta el día.
- Al reconectar, y en cada `manage` del session combo, se pide `GET /v1/market-data/accounts/{id}/candles` con `instrument`, `timeframe=M5`, `price_type=bid`, `from` y `to`. La vela es `{time, open, high, low, close, volume}`. `candles()` devuelve esas velas. El combo las usa aunque la bandera del feed esté apagada.
- Con `AI_FLOOR_BROKER=exness` y el transporte real, el proceso abre el socket de ticks, manda el subscribe y lo vuelve a abrir si cae o si pasan 15 s sin un frame. Eso mantiene el bid/ask fresco. La bandera del feed no hace falta para el socket. Aceptar el socket no reinicia el contador: hace falta un tick y 5 s abierto. La espera es 1, 2, 4, 8, 16 y 30 s. Un cierre inmediato no firma un handshake cada segundo.

## Lo que sigue abierto

- El tick confirmado es `{instrument, bid, ask, timestamp}`. Un frame con otra forma se ignora.
- `websockets==17.2` está pinado. El alta real usa el subprotocolo `exness-ws-protocol`, sin deflate, y firma el handshake como GET. Los tests cubren ese alta con un socket inyectado.

Logs: `M5_FEED_ON` / `M5_FEED_OFF`, `M5_BAR`, `M5_GAP`, `STREAM_SUBSCRIBE`, `STREAM_DROP`. Sin secretos ni id de cuenta.
