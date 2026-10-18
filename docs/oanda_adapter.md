# Adaptador OANDA v20 PRACTICE + enrutado demo (BORRADOR, flag OFF por defecto)

_2026-10-17 · rama `wip/oanda-adapter-clean` · solo `EURUSD` · solo cuenta practice._

## Propósito

Segundo adaptador `BrokerPort` (además de DXtrade) para operar en la cuenta **practice** (demo) de OANDA vía REST v20.
Nada lo importa desde `runtime/`, `floor/`, `ui/`, `ejecutar_*`, `servicio.py`, `app.py` ni `main.py`, con dos
excepciones perezosas y protegidas (verificadas por `test_broker_wiring.py`): `runtime/service.py` importa
`execution.broker_demo_router.router_from_env` **solo** si `AI_FLOOR_BROKER_DEMO_ROUTING` vale exactamente `1`, y la
pantalla Control (solo lectura) lee `read_control_snapshot`, `read_router_status` y `cached_account_view`.
`REAL_EXECUTION_ENABLED` sigue `False`.

| Módulo | Contenido |
|---|---|
| `execution/oanda_client.py` | `OandaClient`: HTTP stdlib, transporte/reloj/sleep/limitador inyectables, token `Bearer` solo en memoria. |
| `execution/oanda_broker.py` | `OandaBroker` (`BrokerPort`), `size_units`, `client_extension_id`, `reconcile` → `ReconcileReport`, `unsupported_open_trades()`; lecturas `open_trades()`, `get_quote()`, `trade_id_for_client()`. |
| `execution/broker_demo_router.py` | `BrokerDemoRouter` (solo vía `router_from_env`): enrutado de planes APPROVED de EURUSD, dry-run, regla de duración, conciliación/drift, cierre de emergencia. |
| `execution/broker_demo_state.py` | Tablas `broker_demo_trades` / `broker_demo_events` (append-only) / `broker_demo_flags` en `broker_state.db`; `read_router_status()` (solo lectura). |
| `execution/oanda_readonly.py` | `OandaReadOnlyClient`: SOLO `GET /v3/accounts/{id}/summary` (practice o live) para la vista Demo/Real del dashboard. Aislado del camino de órdenes. |
| `scripts/oanda_first_test_order.py` | UNA orden de prueba explícita (dry-run por defecto). **No se ha ejecutado.** |

## Configuración (solo nombres en `.env.example`)

| Variable | Default | Notas |
|---|---|---|
| `AI_FLOOR_BROKER_DEMO_ENABLED` | `0` | Solo `1` habilita el constructor. |
| `AI_FLOOR_BROKER_DEMO_PROVIDER` | `dxtrade` | `dxtrade` u `oanda`; cualquier otro valor ⇒ `dxtrade`. `OandaBroker` exige exactamente `oanda`. `read_control_snapshot()` expone `provider` y la etiqueta `DEMO broker (OANDA practice)`. |
| `OANDA_PRACTICE_TOKEN` | vacío | Secreto. Se lee del mapping `env` inyectado, se quitan espacios; nunca en logs/repr/excepciones. |
| `OANDA_ACCOUNT_ID` | vacío (obligatorio) | Solo desde env (sin valor por defecto en el código: el repo es público). Ausente ⇒ `OandaBroker` lanza `BrokerGuardError`, el router queda `None` (aviso `reason=OANDA_ACCOUNT_ID_missing`) y el dashboard muestra «sin cuenta». Debe cumplir `^101-\d{3}-\d+-\d{3}$` (practice). Cuentas live `001-…` rechazadas. Se muestra enmascarada (p. ej. `101-…4567-001`). |
| `AI_FLOOR_EMERGENCY_STOP` | `0` | Activo ⇒ constructor, `submit_plan` y `modify_stop` rechazan. `close_market` siempre permitido. |
| `AI_FLOOR_BROKER_DEMO_ROUTING` | `0` | **El único interruptor.** Solo `1` exacto construye el router. Ver «Enrutado demo». |
| `AI_FLOOR_BROKER_DEMO_DRY_RUN` | vacío | Opt-in: `1` (o true/yes/on) ⇒ dry-run (sin POST/PUT). Vacío ⇒ órdenes REALES en la cuenta PRACTICE. |
| `AI_FLOOR_BROKER_DEMO_MAX_UNITS` | `100000` | Tope de unidades por orden; inválido ⇒ default. |
| `OANDA_LIVE_TOKEN` | vacío | Opcional, solo para **leer el saldo** de la cuenta live en la vista Real (GET summary). Nunca se usa para operar ni se mezcla con el token practice. Usar un token de solo lectura / alcance restringido si OANDA lo ofrece; nunca en el repo. |
| `OANDA_LIVE_ACCOUNT_ID` | vacío | Cuenta live `001-…`. Sin ella la vista Real muestra «sin cuenta» (no se descubre con `GET /v3/accounts`). |

**No existe** `OANDA_BASE_URL` ni ningún flag para live: el host `https://api-fxpractice.oanda.com/v3` está fijo en el
código. `api-fxtrade.oanda.com`, `http`, userinfo, puertos, punto final, mayúsculas u otros hosts ⇒ `BrokerGuardError`
(en el constructor y en cada request). El transporte urllib por defecto no sigue redirecciones (3xx ⇒ `BrokerUnavailable`).
Streaming no implementado.

## Guardas (constructor, antes de cualquier red)

`REAL_EXECUTION_ENABLED is False` · `AI_FLOOR_BROKER_DEMO_ENABLED=1` · provider `oanda` · `mode == "broker_demo"` ·
sin emergency stop (env, DB ni latch) · registro con `broker_demo` ON · token presente · cuenta practice válida.

## Órdenes

- `ALLOWED_SYMBOLS = ("EURUSD",)` → `EUR_USD`. `XAUUSD`, `NAS100`, `EUR_USD`, minúsculas, etc. ⇒ `REJECTED symbol_not_supported` sin red.
- `quantity` = unidades aprobadas por el Risk Engine; debe ser entera (`quantity_not_integer`, no se redondea).
- Precios redondeados a 5 decimales (`ROUND_HALF_UP`); si el redondeo rompe la geometría ⇒ `price_rounding_breaks_geometry`.
- `MARKET`, `FOK`, `positionFill DEFAULT`, `stopLossOnFill`/`takeProfitOnFill` GTC, unidades con signo (SELL negativo),
  `clientExtensions` y `tradeClientExtensions` = `{id, tag: "aifloor", comment: "run:<source_run_id>"}`.
  `id = client_extension_id(client_id)`: el propio id si es seguro (`[A-Za-z0-9_.-]`, ≤128), si no `aif-<sha256[:32]>`.

### Sizing

`size_units(risk_amount, entry, stop) = floor(risk_amount / |entry − stop|)` (EUR_USD cotiza en USD; supone cuenta en USD).
Resultado < 1 ⇒ `0` (no enviar orden). Entradas no positivas o `entry == stop` ⇒ `ValueError`.

### Compuerta previa (solo lecturas, antes del POST)

1. Idempotencia: `GET trades/@<id>`; si existe ⇒ `ACCEPTED already_submitted` sin segundo POST (también si aparece en `openTrades` con ese `clientExtensions.id`).
2. Sin hedging/netting (FIFO EE. UU.): trade abierto en EUR_USD en sentido contrario ⇒ `would_hedge_or_net`; mismo sentido ⇒ `position_already_open` salvo `allow_scale_in=True`.
3. `GET pricing`: `tradeable` debe ser booleano; `false` ⇒ `instrument_not_tradeable`; ausente/`null`/texto o bid/ask
   inválidos ⇒ `pricing_malformed` (también estricto en `modify_stop` ⇒ `pre_modify_check_failed:BrokerMalformedResponse`).

Fallo de lectura en la compuerta ⇒ `REJECTED pre_submit_check_failed:<tipo>` (nada se envió).
Justo antes de **cada intento de envío** del POST (y del PUT de `modify_stop`) — después de la espera del rate limiter y
dentro del bucle de reintentos 429 — el cliente llama a `pre_send_guard`, que re-evalúa interruptor/emergencia
(registro + env); si ya no está permitido ⇒ `BrokerGuardError`, no se envía nada más y el latch del submit se libera
(nada se envió o solo hubo 429, que OANDA no procesa). `close_market` nunca pasa por esa guarda (reduce riesgo).

`submit_plan`, `modify_stop` y `close_market` se serializan por instancia con un `threading.RLock` que cubre lecturas de
la compuerta y la escritura (dos submits concurrentes no pueden pasar ambos la compuerta; un modify intercalado no
puede ensanchar el stop).

### Respuestas, timeouts e idempotencia

- `orderFillTransaction` con `id` no vacío y `tradeOpened.tradeID` numérico ⇒ `ACCEPTED` (`broker_ref` = tradeID);
  cualquier otra forma ⇒ `UNKNOWN malformed_fill_evidence`. Cierre: `id` y `tradesClosed` con el tradeID pedido;
  stop: `stopLossOrderTransaction` con `id`, `type == STOP_LOSS` y `price` igual al stop redondeado (si no, `UNKNOWN`).
- `orderCancelTransaction` / `orderRejectTransaction` / 4xx ⇒ `REJECTED` con **solo el código** (`MARKET_HALTED`, `INSUFFICIENT_MARGIN`…; texto libre ⇒ `UNRECOGNIZED_REASON`).
  La transacción solo cuenta si es un objeto con `id` string no vacío y motivo string no vacío (`reason`; en rechazos
  `rejectReason` o `reason`); si no ⇒ `UNKNOWN malformed_reject_evidence` y el latch se mantiene. Igual de estricto en
  cierre (`orderCancelTransaction`) y stop (`stopLossOrderRejectTransaction`).
- 2xx solo con `orderCreateTransaction` o sin evidencia ⇒ `UNKNOWN`.
- Timeout/5xx/transporte en POST/PUT ⇒ **nunca se reintenta**. Tras un POST ambiguo se hace **una** lectura `GET trades/@<id>`:
  existe ⇒ `ACCEPTED reconciled_after_timeout`; no existe ⇒ sigue `UNKNOWN` (`…:not_found_unproven`), porque OANDA pudo no
  haber procesado aún la orden y la ausencia no prueba que no se envió; el llamador concilia después con
  `get_positions()`/`reconcile()`. Nunca se re-postea.
- Latch UNKNOWN: el `clientExtensions.id` se marca "en vuelo" antes del POST y queda marcado si el resultado es
  `UNKNOWN` (`unresolved_client_ids()`). Un `submit_plan` posterior con ese id no postea: solo `GET trades/@<id>`;
  existe ⇒ `ACCEPTED already_submitted` (y se libera); si no ⇒ `UNKNOWN previous_submission_unresolved`. Solo se libera
  con un trade probado o un rechazo de negocio definitivo.
- Cliente: ≤10 lecturas/s, 2 trading/s; 429 ⇒ backoff 1-2-4 s (respeta `Retry-After`, tope 8 s, máx. 3) y luego
  `BrokerRateLimited`; 5xx en GET ⇒ máx. 2 reintentos; 401/403 ⇒ `BrokerAuthError`.

### Cerrar, mover stop, posiciones

- `close_market(tradeID)`: `PUT trades/{id}/close {"units":"ALL"}`; permitido con interruptor OFF/emergencia. 404 ⇒ se
  verifica en `openTrades`: ausente ⇒ `no_open_position`; lectura fallida ⇒ `UNKNOWN`.
- `modify_stop`: lee el trade (abierto, EUR_USD) y el precio; LONG stop < bid, SHORT stop > ask
  (`stop_wrong_side_of_market`); nunca ensancha el riesgo (`would_widen_stop`); `PUT trades/{id}/orders {"stopLoss":…}` (TP intacto).
- `get_positions()`: desde `openTrades` (`position_ref` = tradeID). Instrumentos no mapeables se excluyen con aviso y
  aparecen en `unsupported_open_trades()`; malformado ⇒ `BrokerMalformedResponse`, nunca `[]`.
- `reconcile(local_refs)` ⇒ `missing_at_broker`, `unknown_at_broker`, `ok_refs`, `unsupported`; si la lectura falla, lanza.

## Limitaciones

Solo EUR_USD (oro e índices no disponibles para residentes de EE. UU. en OANDA) · FIFO, sin hedging · una posición neta ·
mínimo 1 unidad · solo practice; host live rechazado y **no existe flag** para habilitarlo · códigos 404
(`TRADE_DOES_NOT_EXIST`/`NO_SUCH_TRADE`) sin confirmar hasta el spike de solo lectura · cuenta en USD asumida ·
**propiedad exclusiva de la cuenta**: un solo proceso con una sola instancia de `OandaBroker`; otro proceso/herramienta
operando la misma cuenta no está soportado (el lock no lo cubre) · el latch UNKNOWN vive **en memoria, por proceso**:
un reinicio lo pierde, así que tras reiniciar hay que conciliar (`reconcile()`) antes de reenviar ese client id.

## Enrutado demo (`execution/broker_demo_router.py`)

**Activar = un solo flag:** `AI_FLOOR_BROKER_DEMO_ROUTING=1` (OFF por defecto en código, `.env.example` y
`render.yaml`, que no tiene ninguna clave de broker). Con el flag a `1` y sin dry-run **se envían órdenes reales a la
cuenta PRACTICE** de OANDA (el host live sigue imposible). Requisitos que el router deriva o comprueba:

- Construye su propio mapping derivado (`AI_FLOOR_BROKER_DEMO_ENABLED=1`, provider `oanda`); `os.environ` no se toca.
- Primera construcción (no existe ninguna fila de auditoría de `broker_demo` ni de emergencia en
  `broker_switches_audit`): enciende `broker_demo` con auditoría (actor `router_from_env`, source `routing_flag`).
  Después manda la DB: si el operador lo apagó, sigue apagado (router `None`); la emergencia siempre gana y
  limpiarla nunca lo re-enciende (cuenta como historial del operador).
- Emergencia activa, token `OANDA_PRACTICE_TOKEN` ausente, cuenta no practice o cualquier error ⇒ `None` + aviso
  enmascarado. Paper sigue igual.
- Modo de broker siempre `broker_demo`. `REAL_EXECUTION_ENABLED=False`.

### Flujo por ciclo

```
run_cycle(símbolo) ──► (paper sin cambios) ──► record_cycle ──► router.on_cycle(cycle_context, estado, ahora)
                                                                 │  (try/except: nunca afecta al ciclo)
  ┌──────────────────────────────────────────────────────────────┘
  ▼
 PLAN_READY? ─no─► IGNORED
  │ sí
 EURUSD? ─no─► NOT_ROUTED symbol_not_supported   (XAUUSD / NAS100 / otros: nunca se envían)
 eligible True, Risk APPROVED (determinista y AI), plan == decisión, geometría ─no─► NOT_ROUTED <motivo>
 client_id = aif-<run_id> ya existe ─► ALREADY_ROUTED (idempotente)
 reconcile(): lectura fallida ⇒ NOT_ROUTED broker_read_failed · drift ⇒ blocked:drift · UNKNOWN/PENDING_SEND ⇒ unresolved_submission
 interruptor/emergencia (registro) ─no─► NOT_ROUTED broker_demo_not_allowed
 posición abierta (fila local o trade EUR_USD en OANDA) ─► NOT_ROUTED position_already_open (una posición neta, sin hedging)
 unidades = min(size_units(NAV × risk_fraction, entrada, stop), MAX_UNITS); 0 ⇒ SKIPPED_ZERO_UNITS
  ▼
 DRY-RUN: evento DRY_RUN con el JSON exacto de la orden, fila DRY_RUN, sin POST/PUT
 LIVE:    fila PENDING_SEND (write-ahead) ─► OandaBroker.submit_plan ─► OPEN(trade_id) | REJECTED(código) | UNKNOWN
```

- No depende de que paper haya enviado su orden, pero respeta el mismo `eligible` y el Risk APPROVED.
- `risk_fraction` = `RiskDecision.risk_fraction` si está en (0, 0.01], si no 0.01 (tope). La `RiskApproval` se
  deriva de la decisión aprobada con la cantidad re-dimensionada; entrada/stop/meta idénticos (5 decimales).
- UNKNOWN (o PENDING_SEND tras un crash) bloquea todo enrutado nuevo hasta que `reconcile()` lo resuelve
  (encontrado por `clientExtensions.id` en `openTrades` o `GET trades/@id`). Si OANDA demuestra que no existe, el
  operador puede liberarlo con `mark_not_sent(client_id, actor, motivo)` (vuelve a comprobarlo antes).
- Estado: `broker_demo_trades(client_id PK, run_id, symbol, side, units, entry, stop, target, initial_stop, trade_id,
  fill_price, opened_at, stage, status, closed_reason, pending_action, action_status, created_at, updated_at)` y
  `broker_demo_events(ts, kind, client_id, detail JSON)` append-only, sin secretos. Nunca la DB del experimento.

### Regla de duración (`manage`, llamada al inicio de `manage_trade_duration` y en cada ciclo con trades abiertos)

Funciones puras de `execution/trade_duration.py` mediante `PositionView` (`duration_action(fila, ahora, precio)`):
próximo límite = cierre NY del Día 1 (17:00 America/New_York tras `openTime`; DST correcto). En/tras un límite no
procesado: viernes ⇒ cerrar; etapa Día 2 ⇒ cerrar; Día 1 por debajo de +0.5R (R del stop ORIGINAL) ⇒ `close_market`;
si no ⇒ se mantiene y `modify_stop` a la entrada (etapa `DAY2_BREAKEVEN`, se guarda `initial_stop`). Máximo 2 días
(un trade de Día 1 que ya pasó su cierre de Día 2 se cierra). Precio = bid (LONG) / ask (SHORT) de OANDA.
Toda acción (cierre de viernes, Día 2, Día 1, breakeven) exige una cotización válida: pricing leído y parseado sin
error **y** `tradeable` exactamente `true`. Pricing caído/malformado/`None` o `tradeable` distinto de `true` ⇒ ninguna
acción ese tick, el límite sigue vencido y se reintenta en cada tick (evento `MANAGE_SKIPPED` con el código, una vez por
tick). Client ids deterministas `<id>-close`, `<id>-be`, `<id>-emergency`. Resultado UNKNOWN ⇒ latch
(`action_status`), sin repetir; ver conciliación.
Sin gestión en dry-run (solo lecturas de conciliación).

**Ojo (decisión del owner):** OANDA cierra EUR_USD a las 17:00 ET del viernes, justo el límite del cierre de viernes;
en la práctica ese cierre puede quedar pendiente hasta la reapertura del domingo (riesgo de gap de fin de semana; el
SL/TP del broker sigue activo).

### Conciliación y drift

`reconcile()` (al inicio de `manage` y antes de enrutar): fila OPEN ausente en OANDA ⇒ `CLOSED_AT_BROKER`
(`SL_TP_OR_EXTERNAL`; o `CLOSED` con el motivo si había un cierre UNKNOWN pendiente); UNKNOWN/PENDING_SEND encontrado ⇒
OPEN; trade en OANDA que no corresponde a ninguna fila (incluidos instrumentos no EUR_USD y el trade del script de
prueba) ⇒ evento `DRIFT` y `blocked_reason="drift"` (persistido) que bloquea el enrutado hasta
`clear_drift(actor, motivo)`. Lectura fallida ⇒ nunca marca nada como ausente y bloquea ese tick.
Una acción UNKNOWN (cierre/breakeven/modify) **no** se libera porque el trade siga abierto: solo con evidencia
definitiva — cierre ⇒ trade ausente en OANDA; breakeven ⇒ `stopLossOrder.price` del trade igual al stop objetivo
(entrada, 5 decimales) ⇒ etapa `DAY2_BREAKEVEN`. Mientras tanto la fila queda UNKNOWN, sin reintentos, con evento
`UNRESOLVED_ACTION` y `blocked_reason="unresolved_action"` (bloquea enrutado nuevo) hasta la evidencia o
`clear_unresolved(actor, motivo)` del operador (libera el latch; `manage` puede reintentar la acción).

### Interruptor y emergencia

Toda apertura/modificación re-chequea interruptor y emergencia (registro y `OandaBroker`). Cerrar siempre está
permitido. `emergency_close_all(reason)` cierra todos los trades OPEN enrutados; es candidato a hook `on_emergency`
del registro (firma `on_emergency(reason=...)`), **no** se registra automáticamente. En dry-run no envía ningún
PUT/POST: solo registra un evento `DRY_RUN` (vista previa) por fila OPEN. El router en dry-run no crea órdenes en el
broker; los trades abiertos fuera del router (o en una ejecución live anterior) se cierran a mano o con el script
`--close`. Si el interruptor está OFF al
arrancar, el router no se construye: el trade abierto conserva su SL/TP en OANDA y se cierra con el script `--close`.

### Hooks del runtime (`runtime/service.py`, mínimos y aditivos)

`OperationalRuntime(..., broker_router=_AUTO)`: con `_AUTO` construye el router solo si el flag es `1` (import perezoso
en try/except). Tras el registro del ciclo: `router.on_cycle(...)` vía `_audit_safely` (nunca lanza, no cambia el
resultado; no se llama en `DUPLICATE`/`HALT_REFUSED` ni tras el halt). `manage_trade_duration`: `router.manage(at)`
antes del retorno temprano de paper (funciona aunque paper esté apagado); el resultado de paper es idéntico.

### Dashboard (pantalla Control, solo lectura)

Chip «Enrutado a broker demo» (apagado / dry-run / activo; activo solo con heartbeat del router < 20 min en la DB),
bloqueo, trade abierto y últimos eventos; vista **Demo | Real** (por defecto Demo) con balance/NAV/margen/resultado
abierto y «actualizado hace Ns», auto-refresco cada 10 s con caché ≥ 5 s. **Real = solo lectura (operar bloqueado)**:
`execution/oanda_readonly.py` solo hace `GET /v3/accounts/{id}/summary`; sin token live ⇒ «sin token»; nunca usa
`OANDA_PRACTICE_TOKEN` para live ni al revés; cuentas practice `101-`, live `001-`. Sección «📚 Historial (referencia /
baseline)»: vacía, separada de las estadísticas de la demo en vivo; se cargará más adelante.

### Límite de pérdida diaria (borrador, OFF por defecto)

`execution/daily_loss_limit.py` (decisiones D2/D3 del 17 oct 2026, plan de preparación sección 4.1). Solo se construye
si `AI_FLOOR_DAILY_LOSS_LIMIT` vale exactamente `1`; con cualquier otro valor el router es idéntico al de hoy (sin
lecturas extra de NAV ni flags escritos).

| Variable | Defecto | Significado |
|---|---|---|
| `AI_FLOOR_DAILY_LOSS_LIMIT` | vacío | Solo `1` exacto activa el límite diario + halt de drawdown. |
| `AI_FLOOR_DAILY_LOSS_FLATTEN` | vacío (ON) | Al disparar el límite diario se cierra todo lo abierto; solo `0` exacto lo apaga (entonces solo se paran entradas). |
| `AI_FLOOR_DAILY_LOSS_PCT` | `0.02` | Fracción opcional, **solo más estricta** (`0 < x ≤ 0.02`); cualquier otro valor ⇒ `0.02`. |
| `AI_FLOOR_DAILY_LOSS_START_EQUITY` | vacío | Base opcional del drawdown 5 %; si falta, el primer NAV observado (persistido). |

- **Día** = desde la frontera de las 17:00 America/New_York (con DST, todos los días del calendario). `E0` se fija en
  la primera evaluación del día y se persiste en `broker_demo_flags` (`dl_day`, `dl_e0`, `dl_transfers`, `dl_halt`,
  `dd_start`, `dd_halt`, `dd_transfers`, `dl_last_nav`, `dl_last_at`, `dl_tx_id`): un reinicio no lo recalcula.
  `E0` = último NAV válido observado (`dl_last_nav`/`dl_last_at`, escritos en cada evaluación) si tiene ≤ 7 días; si
  no existe (primera evaluación) o es más viejo, el NAV actual. Tras una caída que cruce las 17:00, el tramo previo a la
  frontera se atribuye al día nuevo (conservador: una pérdida ocurrida durante la caída no se borra). NAV incluye el
  P&L abierto.
- **Transferencias** (excluidas automáticamente): P&L del día = NAV − E0 − transferencias. Cuando cambia el
  `lastTransactionID` del summary, el router hace una consulta **solo lectura** `GET
  /v3/accounts/{id}/transactions/sinceid?id=<dl_tx_id>&type=TRANSFER_FUNDS` (mismo guard de host practice, sin
  endpoints de órdenes), suma `amount` de las transacciones ≤ el id del summary y llama a `apply_transfer(neto, id,
  actor="broker", motivo="TRANSFER_FUNDS")`: ajuste + cursor `dl_tx_id` + evento `TRANSFER_RECORDED` en UNA
  transacción sqlite (un fallo no deja ninguno ⇒ el reintento nunca duplica; `record_transfer` manual usa el mismo
  mecanismo). El primer id visto solo se guarda (nunca retroactivo);
  sin cambio de id no hay llamada extra. Si el día actual aún no tiene E0, la transferencia ajusta la base pendiente
  (`dl_last_nav`) del próximo E0. La base del drawdown también la excluye (`dd_transfers`). Si la consulta falla ⇒
  entradas rechazadas (`daily_loss_unavailable`), el id no avanza y el NAV sin ajustar NO se evalúa (no crea halts
  nuevos: un retiro no conciliado no es pérdida); los halts ya activos (latch/flags) siguen aplanando.
- **Halt diario**: P&L ≤ −2 % × E0 (el borde exacto dispara; las ganancias no suben el suelo) ⇒ evento
  `DAILY_LOSS_HALT`, sin entradas nuevas y, con flatten ON, cierre de los trades OPEN enrutados (`close_market`, id
  `…-dlimit`, motivo `DAILY_LOSS`, evento `FLATTEN_DAILY_LOSS`; reintento en cada tick; filas con acción UNKNOWN se
  saltan; en dry-run solo eventos `DRY_RUN`). Antes de cada entrada: `NOT_ROUTED daily_loss_budget` si el riesgo
  planificado (unidades × |entrada − stop|) supera el presupuesto restante.
- **Reanudación**: automática en la siguiente frontera de las 17:00 NY (`DAILY_LOSS_RESUMED`, nuevo E0); antes, solo
  con `clear_daily_halt(actor, motivo)` (auditado; vuelve a dispararse si sigue en pérdida).
- **Drawdown 5 %** (NAV ≤ base × 0,95): `DRAWDOWN_HALT`, cierre siempre (motivo `DRAWDOWN`, aunque flatten esté OFF) y
  **sin** reanudación automática: exige `clear_drawdown_halt(actor, motivo)` tras revisión escrita. Gana el más estricto.
  Un NAV finito ≤ 0 cuenta como ruptura de drawdown (halt + cierre).
- **Latches**: cada halt nuevo se fija primero en memoria (por tracker y por ruta del DB de estado, local al proceso,
  como el latch de emergencia) y después se escribe el flag; si la escritura falla el halt sigue vigente (y se reintenta
  la escritura). El latch de drawdown solo lo libera `clear_drawdown_halt`; el diario, la siguiente clave de día o
  `clear_daily_halt`.
- Se evalúa en cada ciclo, en cada tick de `manage` (aunque paper esté apagado) y antes de cada orden. Fallo al leer el
  NAV, NAV no numérico/NaN/inf o estado ilegible ⇒ se rechazan entradas (`daily_loss_unavailable` / `nav_invalid` /
  `state_unreadable`); si ya hay un halt activo (`halt_state()`: latch o flag) el cierre obligatorio continúa en cada
  tick (drawdown siempre; diario con flatten ON).
- **Independiente del interruptor/emergencia**: nunca escribe `blocked_reason` ni llama al registro; limpiar el
  emergency no limpia el halt y viceversa. Cerrar siempre está permitido. Eventos solo con importes y claves de día.
- **Activarlo es un paso separado del owner**: poner `AI_FLOOR_DAILY_LOSS_LIMIT=1` en Render con un PUT por clave
  (nunca reemplazando todo el entorno) + deploy, con aprobación escrita. Este cambio no lo activa.

### Parada de emergencia, drift y cierre del viernes (borrador)

| Variable | Defecto | Significado |
|---|---|---|
| `AI_FLOOR_EMERGENCY_BUTTON` | vacío (ON) | Botón de parada del panel activo; solo `0` exacto lo desactiva (acción que aumenta la seguridad). |
| `AI_FLOOR_DRIFT_CLEAR_BUTTON` | vacío (OFF) | Solo `1` exacto muestra el botón para pedir la limpieza del drift. |
| `AI_FLOOR_FRIDAY_PREFLATTEN` | vacío (OFF) | Solo `1` exacto activa el cierre previo del viernes. OFF ⇒ router idéntico. |
| `AI_FLOOR_FRIDAY_PREFLATTEN_MINUTES` | `30` | Minutos antes de las 17:00 NY del viernes; entero 5..240, si no ⇒ 30. |

- **Parada de emergencia** (`execution/operator_actions.py`): el panel pide escribir `PARAR TODO` y confirmar; luego
  `request_emergency_stop` apaga todas las cuentas y deja el emergency latcheado en `broker_state.db` (auditoría con
  actor `dashboard`, origen `dashboard`) y escribe el flag `flatten_request`. El router (otro proceso) lo procesa al
  inicio de cada `on_cycle`/`manage`: primero concilia (un envío PENDING_SEND/UNKNOWN ya ejecutado pasa a OPEN) y
  luego cierra los trades OPEN enrutados (id `…-ebtn`, motivo `EMERGENCY_BUTTON`; filas con acción UNKNOWN se saltan;
  en dry-run solo eventos `DRY_RUN`), reintenta en cada tick y borra el flag (`FLATTEN_REQUEST_DONE`) solo si la
  conciliación de ese tick funcionó y no queda ninguna fila OPEN/PENDING_SEND/UNKNOWN. Lectura fallida ⇒ el flag se
  conserva (`FLATTEN_REQUEST_PENDING` con `broker_read_failed`). **Quitar la parada no es posible desde el panel.**
- **Drift**: con `blocked_reason = drift` el panel muestra «Bloqueo por drift». Con el botón activo, escribir
  `LIMPIAR DRIFT` crea `drift_clear_request`. `reconcile()` solo limpia si en esa misma pasada la lectura del broker
  funcionó, no hay drift (todo trade abierto en el broker casa con una fila local), no hay filas PENDING_SEND/UNKNOWN
  ni acciones UNKNOWN (evento `DRIFT_CLEARED`); si no, `DRIFT_CLEAR_REFUSED` con los motivos. La solicitud se consume
  siempre (hace falta otra). Sin solicitud nunca se limpia. `clear_drift()` en Python no cambia.
  Cada bloqueo por drift abre un episodio nuevo (flag `drift_episode`, token aleatorio); la solicitud copia el episodio
  vigente (sin episodio ⇒ `no_drift`) y solo vale para él: una solicitud de un episodio anterior se rechaza
  (`stale_episode`) y se borra. Limpieza/rechazo, borrado de la solicitud y evento de auditoría van en UNA transacción
  SQLite (`DemoStateStore.apply`): si falla no cambia nada. Un bloqueo por drift anterior a este cambio no tiene
  episodio: se limpia con `clear_drift()`.
- **Viernes** (`execution/friday_close.py`, America/New_York con DST): ventana `preflatten` = viernes desde 17:00 −
  N min hasta 17:00; `closed` = viernes ≥ 17:00 hasta domingo < 17:00. En `preflatten` se cierran los trades OPEN
  enrutados (id `…-fri`, motivo `FRIDAY_PREFLATTEN`) con cotización operable, reintentando en cada tick; en
  `preflatten` y `closed` se rechazan entradas (`friday_close_window`, también en dry-run). Desde las 16:55 NY, si
  queda algún trade: `LOG.error` con `event=ALERT_FRIDAY_FLATTEN_FAILED` + evento `FRIDAY_FLATTEN_FAILED` (cada 5 min;
  cada 30 min con el mercado cerrado, mientras se siguen reintentando cierres que el broker rechaza) y el hook
  opcional `router.on_alert(kind, message)`. La regla de duración (cierre del viernes 17:00) sigue como último respaldo.
- **Reinicio con el emergency activo** (registro o `AI_FLOOR_EMERGENCY_STOP`): si queda un `flatten_request` o filas
  enrutadas OPEN/PENDING_SEND/UNKNOWN, `router_from_env` construye un router `closing_only` sobre un `OandaBroker`
  `close_only=True` (mismos guards de practice/token/cuenta/REAL_EXECUTION, pero sin exigir el switch ON ni la
  ausencia de emergency; `submit_plan`/`modify_stop` siempre lanzan `BrokerGuardError`). Ese router rechaza toda
  entrada (`emergency_active`), concilia y cierra los trades OPEN enrutados en cada tick (sin solicitud: motivo
  `EMERGENCY_ACTIVE`, id `…-emergency`) y nunca enciende `broker_demo`. Sin filas ni solicitud ⇒ `None`, como antes.
  La alerta del viernes solo existe mientras el router corre.
- **Activar** `AI_FLOOR_DRIFT_CLEAR_BUTTON` / `AI_FLOOR_FRIDAY_PREFLATTEN` (o desactivar el botón de parada) es un paso
  separado del owner (PUT por clave en Render + deploy, con aprobación escrita). Este cambio no lo activa.

## Plan para retirar paper más adelante

El router es independiente de `PaperBroker`: no lo importa ni lee su estado, y solo consume `cycle_context`. Para
retirar paper (decisión del owner, fase aparte): borrar `execution/paper_broker_adapter.py` y su test, luego el camino
paper de `runtime/service.py` (submit/gestión paper) manteniendo los dos hooks del router, y `PaperBroker` cuando
nada lo use. El router, su estado y `trade_duration.py` no cambian.

## Plan para la PRIMERA orden de prueba (solo con aprobación explícita de Jeferson)

`scripts/oanda_first_test_order.py` (**no se ha ejecutado**; ninguna orden fue colocada por el agente):

1. Aprobación escrita del owner (fecha, tamaño, ventana horaria).
2. Exportar `OANDA_PRACTICE_TOKEN` en el entorno local (nunca en el repo).
3. `python scripts/oanda_first_test_order.py --status` (solo lectura: resumen, openTrades, pricing; debe estar vacío).
4. `python scripts/oanda_first_test_order.py` (dry-run por defecto: imprime el JSON, EUR_USD 1 unidad, SL 20 / TP 40
   pips desde ask/bid, `clientExtensions.id = aif-test-<UTC>`; no envía nada).
5. Envío: `AI_FLOOR_BROKER_DEMO_ENABLED=1 AI_FLOOR_BROKER_DEMO_PROVIDER=oanda`, `broker_demo` ON en el registro, sin
   emergencia, y **las dos cosas**: `--send --confirm I-APPROVE-PRACTICE-TEST-ORDER` (texto exacto). `--units N`
   (máx. 100, se rechaza >100). Se rechaza si el mercado no es `tradeable`. Nunca imprime token ni cuenta.
6. Vigilarla en la plataforma OANDA practice.
7. Cerrar: `--close <TRADE_ID> --confirm I-APPROVE-PRACTICE-TEST-ORDER`.
8. Verificar `--status` sin trades abiertos antes de activar `AI_FLOOR_BROKER_DEMO_ROUTING=1` (un trade del script
   abierto se vería como drift y bloquearía el enrutado).

## Dashboard notes (env + registry)

* **Env changes need a DEPLOY, not a restart.** On Render a "restart" re-runs the last deploy with the env snapshot taken
  at that deploy. After adding/changing `OANDA_*` variables (per-key PUT) trigger a deploy of the same commit
  (`POST /v1/services/{id}/deploys` with `commitId`) so the dashboard process sees them. The Demo/Real balance panel
  names the missing variable (name only, never a value) when it shows "sin token" / "sin cuenta".
* **Registry on a fresh disk.** The Control view creates `broker_state.db` (every switch OFF, no emergency, no audit
  rows) via `execution.account_registry.ensure_registry_initialized` only when the file does not exist (exclusive,
  `0600`, no symlinks). It never touches an existing file and never enables anything.
* Balance reads are independent of the routing flag and of the `broker_demo` switch (GET summary only).
