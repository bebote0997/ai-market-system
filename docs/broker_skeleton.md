# Esqueleto de broker (BORRADOR, NO activado)

_2026-10-15 · rama `wip/broker-structure` · diseño: plan de integración demo (§2.1–2.3, §5, §8)._

## Qué existe

| Módulo | Contenido |
|---|---|
| `execution/broker_port.py` | `BrokerPort` (Protocol): `submit_plan`, `close_market`, `modify_stop`, `get_positions`, `get_balance`, `health`. Dataclasses inmutables (`OrderPlan`, `RiskApproval` (decisión estructurada del Risk Engine; `submit_plan(plan, approval)` la exige en todos los adaptadores), `SubmitResult`/`CloseResult`/`ModifyResult` con `ACCEPTED|REJECTED|UNKNOWN`, `BrokerPosition`, `BrokerBalance`, `BrokerHealth`), todo en `Decimal`. Errores tipados (`BrokerAuthError`, `BrokerRateLimited`, `BrokerApiNotPermitted`, `BrokerPreconditionFailed`, `BrokerUnavailable`/`BrokerTimeout`, `BrokerRejected`, `BrokerMalformedResponse`, `BrokerGuardError`). |
| `execution/paper_broker_adapter.py` | Envoltorio delgado sobre `PaperBroker` (sin modificarlo; nadie más lo importa: se puede borrar junto con su test). El contexto propio de paper se inyecta en el constructor (`floor_report_for(plan)`, `clock()`, `price_for(symbol)`, `market_context()` opcional), así que solo se usa vía `BrokerPort`; no expone el `PaperBroker`. `submit_plan` exige `RiskApproval` APPROVED y el `floor_report` del proveedor, ambos idénticos al `OrderPlan`; `PaperBroker` aplica sus propias compuertas. `modify_stop` devuelve `REJECTED` (el movimiento a entrada sigue en el runtime). |
| `execution/dxtrade_client.py` | Cliente HTTP stdlib con transporte inyectable; `POST /login` → token solo en memoria (`Authorization: DXAPI …`), `ping`, órdenes `MARKET/LIMIT/STOP`, grupo IF-THEN, `PUT` con `If-Match`, posiciones, métricas. Limitador 1 login/s, 2 datos/s, 1 trading/s; 429 con backoff acotado (1, 2, 4 s; máx. 3 reintentos, respeta `Retry-After`, tope 8 s); 401 → un re-login; 404 code 2 → `BrokerApiNotPermitted`; 412 → `BrokerPreconditionFailed`; timeout → `BrokerTimeout`. Redacta password, token, usuario, `domain` y código de cuenta. Base URL: `https` con host, sin userinfo/query/fragment; `repr` muestra solo el endpoint saneado. |
| `execution/dxtrade_broker.py` | `DxtradeBroker` (adaptador demo). Entrada = grupo IF-THEN (padre MARKET `OPEN` + hijas STOP/LIMIT `CLOSE`). Antes de cualquier red, `submit_plan` exige `RiskApproval` APPROVED idéntica al plan (run_id/símbolo/lado/cantidad/entry/stop/target) y símbolo en `EURUSD`/`XAUUSD` (NAS100 y cualquier otro → `REJECTED`). Trading: timeout/5xx/transporte → `UNKNOWN` (sin reintento); 2xx sin `orderId` explícito (`{}`, `[]`, forma desconocida) → `UNKNOWN`; 4xx de negocio → `REJECTED`. Posiciones malformadas (colección ausente o de otra forma, lado desconocido, cantidad inválida) → `BrokerMalformedResponse`; `[]` solo para colección vacía válida; `close_market` con estado desconocido → `UNKNOWN` (nunca `no_open_position`). `modify_stop` relee la versión y reintenta una vez ante 412. Símbolos sin mapear → `REJECTED`. |
| `execution/broker_config.py` | Lectura de env con defaults seguros; `repr` redacta secretos y enmascara la cuenta (`DEM_…12_1`). |
| `execution/account_registry.py` | Registro `paper`/`broker_demo`/`real`, interruptores, auditoría append-only (triggers), `emergency_state`, `AccountRunner` (aislamiento + circuit breaker por cuenta) y `read_control_snapshot()` para la UI. |

Tests: `test_broker_paper_adapter.py`, `test_broker_dxtrade.py`, `test_broker_registry.py`, `test_broker_wiring.py` (siempre con transporte falso; el transporte urllib por defecto está parcheado para fallar).

## Qué NO está conectado

- Nada en `runtime/`, `floor/`, `ejecutar_*`, `servicio.py`, `app.py` ni `main.py` importa estos módulos (lo verifica `test_broker_wiring.py`).
- Única excepción (rama combinada): `ui/views/control.py` importa de forma perezosa y protegida solo `read_control_snapshot` (solo lectura; si falla, todo se muestra apagado). Lo fija `test_broker_wiring.py`.
- `PaperBroker`, `render.yaml` y la DB del experimento no se tocan. `REAL_EXECUTION_ENABLED` sigue `False`.
- No hay modo sombra, write-ahead (`broker_intents`), conciliación, tamaño demo ni traducción Day-1.
- Rutas y formatos DXtrade (`orders/group`, `metrics`, campos `version`, `positionCode`, `errorCode`) **sin confirmar** hasta el spike de solo lectura.

## Variables de entorno (solo nombres en `.env.example`)

| Variable | Default | Notas |
|---|---|---|
| `AI_FLOOR_BROKER_DEMO_ENABLED` | `0` | Solo el valor exacto `1` habilita el constructor de `DxtradeBroker`. |
| `AI_FLOOR_BROKER_REAL_ENABLED` | — | Se lee y **se ignora** siempre (`BrokerConfig.real_enabled` es `False`). |
| `AI_FLOOR_EMERGENCY_STOP` | `0` | `0/false/no/off`/vacío = inactivo; `1/true/yes/on` = activo; cualquier otro valor = **activo**. |
| `AI_FLOOR_BROKER_STATE_DB_PATH` | `data/runtime/broker_state.db` | Rechaza la DB del experimento (`AI_FLOOR_DB_PATH` o nombre `trading_floor.db`). |
| `DXTRADE_BASE_URL` | `https://api.liquidcharts.com/dxsca-web/` | Solo `https` con host; userinfo, query o fragment ⇒ rechazado. |
| `DXTRADE_USERNAME`, `DXTRADE_PASSWORD`, `DXTRADE_DOMAIN` | vacío | Secretos (incluido `domain`); nunca en logs/repr/excepciones. |
| `DXTRADE_ACCOUNT_CODE` | vacío | Debe empezar por `DEM_`; `ECN_`/`VIP_`/`NOC_` rechazados. |

## Guardas de `DxtradeBroker` (constructor, antes de cualquier red)

`REAL_EXECUTION_ENABLED is False` · `AI_FLOOR_BROKER_DEMO_ENABLED=1` · `mode == "broker_demo"` · cuenta = `DXTRADE_ACCOUNT_CODE` exacta, prefijo `DEM_`, sin prefijos reales · sin emergency stop (env ni DB) · registro presente con `broker_demo` ON · credenciales completas. Después, `submit_plan`/`modify_stop` vuelven a comprobar interruptor y emergencia; `close_market` siempre está permitido (reduce riesgo).

## Emergency stop

`AccountRegistry.emergency_stop(actor=..., reason=...)`: **primero** activa un latch en memoria del proceso (por ruta de DB) y **después** escribe; en una sola transacción SQLite pone `enabled=0` en todas las cuentas, `emergency_state.active=1` y escribe la auditoría; luego llama el hook opcional `on_emergency(reason=...)` (por defecto no-op; aquí irán cierre/cancelación). Todas las comprobaciones de autorización del proceso (`is_allowed`, `emergency_active`, `set_enabled(True)`, guardas de `DxtradeBroker`, `read_control_snapshot`) respetan el latch aunque la escritura haya fallado. Si no puede persistir, el hook se ejecuta igual, se registra `CRITICAL` y lanza `BrokerGuardError`. Persiste entre reinicios; `AI_FLOOR_EMERGENCY_STOP` también lo activa. `clear_emergency` es la única recuperación explícita: persiste todos los interruptores en OFF y la bandera en OFF y solo entonces suelta el latch (si la DB sigue rota, el latch sigue activo); ningún interruptor se reactiva solo.

**Si falla la persistencia, otros procesos NO ven el latch** (es memoria local). Solo quedan detenidos si a ellos también les resulta ilegible la DB (fallan cerrado). Procedimiento obligatorio ante el `BrokerGuardError`/log `CRITICAL`: (1) poner `AI_FLOOR_EMERGENCY_STOP=1` en el entorno de cada proceso que use el broker y reiniciarlo, o detener esos procesos (lo honran el registro, el constructor y cada `submit_plan`/`modify_stop`); (2) reparar la DB de estado; (3) `clear_emergency(...)` y, solo tras revisar, quitar `AI_FLOOR_EMERGENCY_STOP`. Con emergencia activa no se puede habilitar ninguna cuenta. `real` nunca se puede habilitar.

`read_control_snapshot(path=None)`: solo lectura (`mode=ro`, no crea el archivo), nunca lanza; devuelve `readable`, `emergency_stop`, `connected` (siempre `False`) y las tres cuentas (`real` bloqueada). DB ausente/ilegible ⇒ `readable=False`, `emergency_stop=True`, todo apagado. `enabled` es el valor efectivo (con emergencia activa, `False`).

## Qué falta

Spike de solo lectura (confirmar endpoints, `domain`, tamaños, netting/hedging, idempotencia), decisión del owner (DEC) para ejecución en demo de broker, decisión D-1 (dónde corre el espejo), modo sombra y uso de `shadow_only`, write-ahead + conciliación, tamaño demo, traducción Day-1, ping periódico, hook real de cierre en emergencia, controles de UI con reautenticación.
