# API de Exness (Trader API) — preparado, apagado

_2026-11-11. No es MetaTrader. No hay puente en el PC ni en Render. El Floor firma peticiones REST y, cuando haya
credenciales, abre el WebSocket de precios. Nada de esto se envía hasta que el owner ponga las dos claves de
ejecución real. `REAL_EXECUTION_ENABLED` sigue `False`._

Fuente: [portal](https://www.exness-api.com/trading/get-started), [ayuda](https://get.exness.help/hc/en-us/articles/27866287512476-Exness-API),
[firma](https://www.exness-api.com/authentication/request-signing),
[descubrimiento](https://www.exness-api.com/trading/get-started/access-point-host-discovery),
[claves](https://www.exness-api.com/authentication/api-key-and-host-setup),
[WebSocket](https://www.exness-api.com/trading/get-started/subscribe-to-websocket-events),
[semántica](https://www.exness-api.com/trading/guides/runtime-semantics),
[fórmulas](https://www.exness-api.com/trading/guides/formulas-calculations).

## Qué hace el Floor

| Módulo | Papel |
|---|---|
| `execution/exness_sign.py` | Cabeceras `EXN-*`. Ed25519 firma los bytes de `EXN-DATA`, no el texto base64url. |
| `execution/exness_client.py` | REST firmado. Descubre el Access Point una vez y lo guarda. La misma clave de idempotencia no se reenvía. |
| `execution/exness_stream.py` | Libro del último tick. Si el socket cae, el tick se borra al momento. Reintento 1, 2, 4, 8, 16, 30 s (política del Floor: el portal solo dice «backoff»). |
| `execution/exness_transport.py` | `FakeExnessTransport` y `RestExnessTransport`. El adaptador solo llama `read_state` y `submit`. |
| `execution/exness_broker.py` | `ExnessBroker`. Mercado = `POST .../positions` con `instrument`, `side`, `volume` y, si hay, `stop_loss_price` / `take_profit_price`. La página no publica comentario ni client id; la clave va en `EXN-IDEMPOTENCY-KEY`. Límite = `POST .../orders`. Cancelar = `DELETE .../orders/{id}`. Cerrar = `DELETE .../positions/{id}`. |
| `execution/exness_brakes.py` | Pérdida diaria 10 USD y drawdown 50 USD desde el saldo inicial. Frenan y aplanan hasta `RESUME_EXNESS`. |
| `execution/exness_check.py` | Lectura de cuenta, especificación y tick. No envía órdenes. |

`AI_FLOOR_BROKER` vacío o `oanda` deja el router de práctica como está. Solo el valor exacto `exness` construye esta sesión, y entonces no construye también el router de OANDA.

Un ACK HTTP 202 no es un fill. El resultado queda `UNKNOWN` hasta un `transaction_event` o el estado de la operación. Un 429 no se reintenta (página de troubleshooting: `REQUEST_RATE_LIMIT` no es reintentable). Un corte de red después de enviar guarda la misma clave.

Sin tick fresco (por defecto 5 s, `AI_FLOOR_EXNESS_QUOTE_STALE_SECONDS`) no hay orden nueva y se avisa `EXNESS_DISCONNECTED` por el mismo canal de alertas del router.

## Credenciales que tiene que crear el owner

Cuenta de la plataforma Exness, no una cuenta MetaTrader. KYC completo (identidad y, si aplica, domicilio). Personal Area → Key Management (el centro de ayuda lo llama también «API page»).

| Variable | Dónde sale | Permiso |
|---|---|---|
| `EXN_API_KEY` | Identificador público de la clave (`exnsk-…` en los ejemplos). No es la clave privada. | Fase 1: el ejemplo oficial pide **Trading read access**. No uses una Info API key para operar: la guía «Place your first trade» dice usar una Exness API key, no una Info API key. |
| `EXN_PRIVATE_KEY` | Si Exness genera el par: 128 hex (32 bytes de semilla + 32 de pública), se enseña una vez. Si eliges «I'll use my own public key»: el PEM PKCS#8 de `private.pem` se queda en el servidor; en Key Management se pega solo la pública SPKI en Base64 con padding. | La privada no se sube cuando el par es tuyo. No va al repo ni a Render en este cambio. |
| `EXN_ACCOUNT_ID` | Cuenta de trading elegida al crear la clave. Entero sin signo, en texto. | La clave solo ve esa cuenta (y las IP permitidas, si el formulario las pide). |
| `EXN_DISCOVERY_BASE_URL` | Host HTTPS del widget DNS de Key Management, sin barra final. Es el Gateway, no el Access Point. | Sirve para `GET /v1/trading/access-point?account_id=`. El `access_point` devuelto es el host de REST y WebSocket. No copies `https://api.exness.com` ni `https://api.exness-api.com`: el portal dice que son ejemplos de revisión, no hosts de respaldo. |

Para la comprobación de solo lectura basta la clave con Trading read access. Para órdenes hace falta una clave con derecho a operar. Los términos piden el mínimo privilegio: no actives trading en la clave que solo lee. Los nombres exactos de las casillas del formulario no están en las páginas leídas (ver TODO).

Comprobación, sin órdenes:

```
python -m execution.exness_check
```

Reanudar un freno, también sin órdenes (la frase no se queda puesta):

```
AI_FLOOR_EXNESS_RESUME=RESUME_EXNESS python -m execution.exness_check --resume
```

Ejecución real, las dos a la vez, y si no el adaptador solo lee:

- `AI_FLOOR_EXNESS_REAL_EXECUTION=1`
- `AI_FLOOR_EXNESS_REAL_CONFIRM=EXNESS_REAL_AUTHORIZED`

Frenos (USD de la cuenta; otra divisa no opera): `AI_FLOOR_EXNESS_DAILY_LOSS_USD` (10), `AI_FLOOR_EXNESS_MAX_DRAWDOWN_USD` (50), `AI_FLOOR_EXNESS_START_BALANCE` (si falta, el primer equity). Estado en `AI_FLOOR_EXNESS_STATE_DB_PATH` (si falta, `data/runtime/exness_state.db`).

Comisión por lote y lado que el owner copia de la cuenta mientras el JSON no tenga un campo documentado: `AI_FLOOR_EXNESS_COMMISSION_PER_LOT_SIDE` (vacío = 0). El spread sale del tick.

La firma en vivo usa el paquete `cryptography` (el ejemplo oficial de Python). No está en `requirements.txt`. Sin él, el transporte real no arranca.

## Tamaño

`lots = floor_to_step(balance * risk / (|entrada − stop| * contractSize * ProfitRate))`. Si queda por debajo de `volume_min`, no hay orden. `contractSize` y `profitRate` son los nombres de la página de fórmulas. Si el JSON real usa otros nombres, el tamaño sale `contract_size_unavailable` o `profit_rate_unavailable` y no se inventa un contrato de 100 000.

## TODO (las páginas no lo fijan)

1. Orden canónico del JSON dentro de `EXN-DATA`. Aquí se usa el orden del ejemplo y separadores compactos.
2. La comisión dentro de instrument conditions sigue sin campo. El contrato vivo es `contract_size`. No hay `profitRate`: USD contra USC vale 100; JPY contra USC vale `100 / USDJPY` solo si ese precio ya está en el libro.
3. `POST /orders` usa `type` (`limit` / `stop` / `sltp`). El SL/TP de una posición es una orden `type=sltp` con `position_id`. `PATCH /orders/{id}` solo cambia una orden pendiente.
4. El cierre parcial es `DELETE /positions/{id}?volume=`. `close_units` lo manda si el volumen es menor o igual al de la posición.
5. Forma JSON de `GET .../limits`. Mientras tanto hay un hueco mínimo de 100 ms (`AI_FLOOR_EXNESS_MIN_INTERVAL_MS`) y un 429 bloquea el envío.
6. El tick vivo es `{instrument, bid, ask, timestamp}`. `bid` y `ask` pueden ser números. `parse_tick_frame` los acepta.
7. Velas: query `instrument`, `timeframe=M5`, `price_type=bid`, `from`, `to`. El objeto es `{time, open, high, low, close, volume}`. `time` es ISO con zona. Los precios pueden ser números.
8. No hay host de sandbox. Un demo de MetaTrader no vale. No está escrito si una cuenta demo de la plataforma Exness puede crear claves.
9. Nombres exactos de los permisos en el formulario (además de «Trading read access» y de la advertencia de no usar una Info API key para operar).
10. `cryptography` sin pin en este repo.
11. El camino de OANDA (retroceso 12,5 pips, parciales, trail) no está reescrito para Exness. Con el flag `exness` y las dos claves, un plan aprobado se manda a mercado con su SL/TP.
