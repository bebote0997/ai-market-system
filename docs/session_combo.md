# Session combo (Exness) — preparado, apagado

Estrategia de cuenta real para el adaptador Exness. No opera salvo que `AI_FLOOR_SESSION_COMBO` valga exactamente `1` y `AI_FLOOR_BROKER` valga exactamente `exness`. Esa bandera sola no envía órdenes: sigue haciendo falta `AI_FLOOR_EXNESS_REAL_EXECUTION=1` y `AI_FLOOR_EXNESS_REAL_CONFIRM=EXNESS_REAL_AUTHORIZED`.

## Reglas

1. Londres, USDJPY. Rango = máximo y mínimo de las velas M5 de USDJPY con apertura en `[19:00, 02:00)` America/New_York (cambio de hora incluido). Si falta la vela de las 19:00 o la de las 01:55, se reintenta en ticks posteriores. A las 02:00 ET, si sigue incompleto, `LONDON_OCO_SKIPPED reason=range_incomplete` y ese día no se reabre. Desde las 02:00 y solo hasta las 05:00 ET se arma el buy-stop en el máximo y el sell-stop en el mínimo. La entrada la dispara la vela M5 que está abierta en ese instante, y solo si el ask (compra) o el bid (venta) ya cruzó el nivel. Una vela ya cerrada no abre el trade. A las 05:00 ET, si no hubo cruce en vivo, se cancela aunque el historial tenga cruces anteriores. Si una vela M5 cruza los dos lados, el día se salta. SL = el otro lado del rango. TP = 2 veces la distancia real entre el fill y ese SL. Cierre a mercado a las 11:00 ET.
2. Nueva York, fade del fix de las 16:00 Europe/London. Primero GBPUSD, luego EURUSD, cada uno con sus propias velas M5 (`M5Bar.symbol`). El movimiento es la apertura de la vela de las 15:45 hasta el cierre de la de las 15:55. Si el valor absoluto es de 15 pips o más, entrada a mercado en contra solo en `[16:00, 16:05)` Londres. Fuera de esa ventana el día queda cerrado y no hay entrada tardía. SL 30 pips desde el fill, sin TP, salida 30 minutos después de la entrada. Si GBPUSD aún no tiene las dos velas, no se salta a EURUSD.
3. Riesgo: el menor entre el 1 % del equity, el 1 % del balance y 5 USD, medido con el precio vivo (ask en largo, bid en corto) y la distancia real hasta el SL. El volumen se trunca al `volume_step`. Si el lote mínimo arriesga más que ese tope, no se envía. Una sola posición entre todos los símbolos. Como máximo un trade de Londres y uno del fix por día. Los frenos de 10 USD y 50 USD y el tick viejo siguen en el broker.

El estado de cada pata (`pending`, `armed`, `submitted`, `filled`, `skipped`, `cancelled`, `done`, el client id, el id de la posición, el volumen y la hora de salida) se guarda en el mismo almacén que los frenos: `combo_lon_YYYY-MM-DD` y `combo_fix_YYYY-MM-DD`. La salida de las 11:00 y la de 30 minutos cierran esa posición. Si el id no quedó guardado, se busca una sola posición con el mismo símbolo, lado, volumen y hora de apertura. Solo se marca el día como terminado cuando esa posición ya no está. Un reinicio no vuelve a mandar el trade.

La página de abrir posición lista `instrument`, `side`, `volume` y los precios `stop_loss_price` / `take_profit_price`. No hay campo de comentario ni de client id. La correlación es la cabecera `EXN-IDEMPOTENCY-KEY`, que el portal devuelve como `client_request_id` en el ACK y en `transaction_event`, no en la posición del snapshot. El cuerpo no lo inventa. Si la segunda lectura del precio hace que el lote pedido pase el menor entre el 1 % y 5 USD, el broker baja el lote o no envía.

## Stops

El portal nombra órdenes `stop`, pero no el campo JSON. No se envía una orden pendiente al broker. El Floor guarda los dos precios y manda un mercado cuando la vela viva cruza un solo lado (`mode=tick_cross` en el log).

## Logs

`SESSION_COMBO_ON` / `SESSION_COMBO_OFF` al crear la sesión Exness. Luego `ASIAN_RANGE`, `LONDON_OCO_PLACED`, `LONDON_OCO_CANCELLED`, `LONDON_OCO_SKIPPED` con `reason=`, `FIX_SIGNAL`, `FIX_ENTRY`, `FIX_SKIPPED`, `TIME_EXIT`, `SIZE_SKIP`. No incluyen clave, secreto ni id de cuenta.

## Lo que aún impide usarlo en vivo

- El runtime llama `manage(at)` sin velas M5. Sin velas el combo registra `SESSION_COMBO_NO_BARS` y no entra. La ruta REST de velas de Exness sigue sin estar en las páginas leídas.
- El libro de ticks del adaptador guarda un solo bid/ask. El fill es ese precio, así que el tick que ve el broker tiene que ser del símbolo que se está entrando.
- El nombre JSON de `contractSize` / `ProfitRate` sigue sin confirmarse. Si el payload no los trae con esos nombres, el tamaño se salta.
- `cryptography` no está pinado, así que el transporte real no firma hasta que se añada.
