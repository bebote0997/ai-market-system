# Session combo (Exness) — preparado, apagado

Estrategia de cuenta real para el adaptador Exness. No opera salvo que `AI_FLOOR_SESSION_COMBO` valga exactamente `1` y `AI_FLOOR_BROKER` valga exactamente `exness`. Esa bandera sola no envía órdenes: sigue haciendo falta `AI_FLOOR_EXNESS_REAL_EXECUTION=1` y `AI_FLOOR_EXNESS_REAL_CONFIRM=EXNESS_REAL_AUTHORIZED`.

## Reglas

1. Londres, USDJPY. Rango = máximo y mínimo de las velas M5 con apertura en `[19:00, 02:00)` America/New_York (cambio de hora incluido). Desde las 02:00 ET se arman un buy-stop en el máximo y un sell-stop en el mínimo. Si una sola vela M5 cruza los dos, el día se salta. Si a las 05:00 ET no hubo cruce, se cancelan. SL = el otro lado del rango. TP = 2 veces esa distancia. Cierre a mercado a las 11:00 ET.
2. Nueva York, fade del fix de las 16:00 Europe/London. Primero GBPUSD, luego EURUSD. El movimiento es la apertura de la vela M5 de las 15:45 Londres hasta el cierre de la vela de las 15:55 (el precio de las 16:00). Si el valor absoluto es de 15 pips o más, entrada a mercado en contra en la apertura de la vela de las 16:00. SL 30 pips, sin TP, salida a los 30 minutos. Solo si no hay posición abierta.
3. Riesgo: el menor entre el 1 % del equity, el 1 % del balance y 5 USD. El volumen se trunca al `volume_step`. Si el lote mínimo arriesga más que ese tope, se salta el día. Una sola posición entre todos los símbolos. Los frenos de 10 USD y 50 USD y el tick viejo siguen en el broker.

## Stops

El portal nombra órdenes `stop`, pero no el campo JSON. No se envía una orden pendiente al broker. El Floor guarda los dos precios y, cuando una vela M5 cruza uno solo, manda un mercado con el mismo SL/TP (`mode=tick_cross` en el log).

## Logs

`SESSION_COMBO_ON` / `SESSION_COMBO_OFF` al crear la sesión Exness. Luego `ASIAN_RANGE`, `LONDON_OCO_PLACED`, `LONDON_OCO_CANCELLED`, `LONDON_OCO_SKIPPED` con `reason=`, `FIX_SIGNAL`, `FIX_ENTRY`, `TIME_EXIT`, `SIZE_SKIP`. No incluyen clave, secreto ni id de cuenta.

## Lo que aún impide usarlo en vivo

- El runtime llama `manage(at)` sin velas M5. Sin velas el combo registra `SESSION_COMBO_NO_BARS` y no entra. La ruta REST de velas de Exness sigue sin estar en las páginas leídas.
- El nombre JSON de `contractSize` / `ProfitRate` sigue sin confirmarse. Si el payload no los trae con esos nombres, el tamaño se salta.
- `cryptography` no está pinado, así que el transporte real no firma hasta que se añada.
