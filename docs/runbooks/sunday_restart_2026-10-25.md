# Runbook — reinicio del demo OANDA, domingo 2026-10-25

**Alcance:** desplegar `feat/fx-sl-week-hold` (DEC-FX-SL-1, DEC-HOLD-WEEK-1, DEC-REVERSAL-1, la vista Floor de PR #54,
el botón «Cerrar operación» DEC-MANUAL-CLOSE-1 y el modo solo-OANDA DEC-OANDA-ONLY-1) y, **solo con la decisión de
enrutado del paso 0**, volver a encender scheduler + runner cloud (apagados el 2026-10-22) junto con el enrutado y
reiniciar el reloj de 30 días del demo OANDA practice con una época nueva.
**Lo ejecuta el owner** (o un agente solo con GO explícito para cada paso). Este documento no autoriza nada por sí mismo.
**Invariantes:** `REAL_EXECUTION_ENABLED = False` (constante en código) · solo OANDA **practice**
(`api-fxpractice.oanda.com`) · NAS100 bloqueado · XAUUSD pausado por código (DEC-XAU-OFF-1) · una sola instancia.
Este documento no contiene secretos, ids de cuenta ni URLs privadas: `<SERVICE_ID>`, `<SERVICE_URL>`, `<RENDER_API_KEY>`
y `<MERGE_SHA>` son marcadores que el owner rellena fuera del repo.

## 0. Requisito previo de decisión (bloqueante)

DEC-DEMO-PAUSE-1 (2026-10-20, rama `docs/dec-demo-pause-1`; no está en esta rama) pausó el enrutado
(`AI_FLOOR_BROKER_DEMO_ROUTING=0`) y dice que reanudarlo requiere **una decisión nueva del owner con una estrategia que
pase el backtest**. Orden del owner del 2026-10-22: el domingo se pone `AI_FLOOR_BROKER_DEMO_ROUTING=1` con la
estrategia de esta rama (DEC-FX-SL-1 / DEC-HOLD-WEEK-1 / DEC-REVERSAL-1). Esa reanudación **ya está registrada** como
**DEC-DEMO-RESUME-1** en `docs/decisions.md` (cierra DEC-DEMO-PAUSE-1; paso 0 hecho en esta rama).

**Regla del owner (2026-10-22): nunca ciclos solo-PAPER** (queman tokens de IA sin resultado OANDA). Por eso scheduler,
runner y enrutado van **juntos**:

- **Con la decisión del paso 0:** `AI_FLOOR_SCHEDULER=1`, `AI_FLOOR_CLOUD_RUNNER=1`, `AI_FLOOR_BROKER_DEMO_ROUTING=1`,
  `AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`, `AI_FLOOR_DEMO_EPOCH`, `AI_FLOOR_DEMO_START_EQUITY` (y
  `AI_FLOOR_DAILY_LOSS_START_EQUITY` si aplica).
- **Sin ella:** scheduler, runner y enrutado **se quedan en `0`** (solo dashboard, sin ciclos IA ni gasto); la única
  clave que se pone es `AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`. Merge + deploy se pueden hacer igual: el dashboard, el Floor
  y Slack muestran «sin datos» del demo OANDA (nunca PAPER).

**Coste de IA:** el 2026-10-22 el owner puso `AI_FLOOR_SCHEDULER=0` y `AI_FLOOR_CLOUD_RUNNER=0` para no gastar tokens
de IA hasta el reinicio. Volver a `1` (solo con el enrutado) reanuda los ciclos IA (y su gasto) desde la primera ranura
programada.

Horario: domingo 2026-10-25 (aún EDT; el cambio de hora es el 2026-11-01). El mercado reabre a las **17:00 ET = 21:00
UTC**. Hasta esa hora el código rechaza entradas (`friday_close_window` / `FRIDAY_FLATTEN_WINDOW`) y no hay ciclos
programados hasta la sesión de Londres del lunes (03:00 ET). Ventana recomendada: 14:00–16:30 ET.

## 1. Qué cambia (resumen para el owner)

| Cambio | Efecto en el demo | Env necesaria |
|---|---|---|
| DEC-FX-SL-1 | EURUSD: SL ajustado a [45, 60] pips, TP fijo 2R (90–120 pips); XAUUSD sin cambio | ninguna (defaults en código) |
| DEC-HOLD-WEEK-1 | Cierre forzado viernes 16:45 ET (o 5 días de trading, lo primero); sin entradas vie 16:45 → dom 17:00 ET; Day-1 < 0.5R desactivado | ninguna |
| DEC-REVERSAL-1 | Router: setup opuesto en beneficio ⇒ cierra y abre la nueva dirección (todos los gates) | ninguna (ON por defecto con el enrutado) |
| Vista Floor (PR #54) | Página de solo lectura | ninguna |
| DEC-MANUAL-CLOSE-1 | Botón «Cerrar operación» en el Floor (owner con contraseña, dos clics; cierra la operación enrutada por la ruta de cierre del router, motivo `MANUAL_CLOSE`, alerta Slack) | ninguna (activo solo con el enrutado ON) |
| DEC-OANDA-ONLY-1 | Dashboard, Floor y resumen diario de Slack muestran solo resultados del demo OANDA (el PAPER sigue interno) | `AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO` |
| Scheduler / runner cloud | Reanudan los ciclos (y el gasto de IA), apagados el 2026-10-22 | `AI_FLOOR_SCHEDULER=1`, `AI_FLOOR_CLOUD_RUNNER=1`, **solo con el enrutado** (paso 0) |

`RuntimeConfig.fingerprint` **no cambia** con este despliegue: el preflight `experiment_resumable` sigue pasando con la DB
actual. `AI_FLOOR_RESULTS_SOURCE` no forma parte del fingerprint (test). `V2_OPPOSITE_SETUP_POLICY`: en producción
(Render) ya vale `STRUCTURE_CHANGE` (puesto antes, DEC-14.1/14.2), así que ya forma parte del fingerprint guardado en
`run_metadata`; este despliegue no cambia esa clave ⇒ el fingerprint no cambia y `experiment_resumable` sigue pasando.
**No** se toca: cambiarla respecto al valor guardado cambiaría el fingerprint y rompería el preflight (el servicio no
arrancaría con la DB actual).

`AI_FLOOR_SCHEDULER` y el fingerprint (verificado en código):

- `scheduler_enabled` (`AI_FLOOR_SCHEDULER == "1"` exacto) **sí** entra en `RuntimeConfig.fingerprint()`
  (`runtime/config.py`), que cada ciclo graba en `run_metadata`.
- Pero `experiment_resumable` (`runtime/cloud.py`, `cloud_preflight`) compara `run_metadata` con
  `replace(config, scheduler_enabled=True).fingerprint()`: fuerza `True`, así que el valor de la env **por sí solo no
  hace fallar** ese check (ni con `0` ni con `1`).
- El arranque (`runtime/cloud.py` `serve`) solo admite dos combinaciones: `AI_FLOOR_CLOUD_RUNNER=1` exige
  `AI_FLOOR_SCHEDULER=1` (si no: `RuntimeError("runner requested with scheduler disabled")`) y el preflight de activación
  `experiment_ready`; `AI_FLOOR_CLOUD_RUNNER=0` exige `infra_ready`, que incluye `scheduler_not_started` (scheduler **y**
  runner en `0`). Es decir: **ambos `1` o ambos `0`**; `SCHEDULER=1` con `RUNNER=0` ⇒ «cloud infrastructure preflight
  not ready» y el servicio (dashboard incluido) no arranca.
- Riesgo real de fingerprint: un ciclo ejecutado con `AI_FLOOR_SCHEDULER` ≠ `1` (p. ej. `python -m runtime --once` a
  mano en el shell de Render con la env actual en `0`) graba en `run_metadata` el fingerprint con
  `scheduler_enabled=False`; desde entonces `experiment_resumable` falla (todas las filas deben coincidir) y el runner ya
  no se puede activar con esta DB. Con scheduler/runner en `0` **no** lanzar ciclos manuales.

## 2. Cambios de env en Render (solo nombres y valores previstos)

Siempre **PUT por clave**: `PUT https://api.render.com/v1/services/<SERVICE_ID>/env-vars/<KEY>` con cuerpo
`{"value": "<valor>"}` y cabecera `Authorization: Bearer <RENDER_API_KEY>`. **Nunca** el PUT masivo de
`/env-vars` (reemplaza la lista completa y borraría secretos). Anotar antes el número de claves:
`GET /v1/services/<SERVICE_ID>/env-vars` (paginado; contar `envVar.key`). **Estado de producción verificado:** **38**
claves, `AI_FLOOR_DEMO_EPOCH` **no existe**, `AI_FLOOR_DAILY_LOSS_LIMIT=1` (ya puesto).

**Plan del domingo: 7 PUT por clave** — `AI_FLOOR_RESULTS_SOURCE`, `AI_FLOOR_DEMO_EPOCH`, `AI_FLOOR_DEMO_START_EQUITY`,
`AI_FLOOR_DAILY_LOSS_START_EQUITY`, `AI_FLOOR_SCHEDULER`, `AI_FLOOR_CLOUD_RUNNER`, `AI_FLOOR_BROKER_DEMO_ROUTING`; de
ellas **4 son claves nuevas** (`RESULTS_SOURCE`, `DEMO_EPOCH`, `DEMO_START_EQUITY`, `DAILY_LOSS_START_EQUITY`) ⇒ recuento
final esperado **42** (38 + 4).

| Clave | Valor previsto | Condición | Motivo |
|---|---|---|---|
| `AI_FLOOR_RESULTS_SOURCE` | `OANDA_DEMO` (exacto) | siempre | DEC-OANDA-ONLY-1: solo resultados del demo OANDA en dashboard, Floor y Slack; el ledger PAPER queda interno |
| `AI_FLOOR_SCHEDULER` | `1` (exacto) | solo con la decisión del paso 0 (si no: se queda `0`) | reanuda los ciclos programados (puesto a `0` el 2026-10-22 para frenar el gasto de tokens de IA); va siempre junto con el runner (ver sección 1) |
| `AI_FLOOR_CLOUD_RUNNER` | `1` | solo con la decisión del paso 0 (si no: se queda `0`) | reanuda el runner cloud continuo (puesto a `0` el 2026-10-22, mismo motivo) |
| `AI_FLOOR_BROKER_DEMO_ROUTING` | `1` | solo con la decisión del paso 0 (si no: se queda `0`) | reanuda el enrutado OANDA practice (habilita también «Cerrar operación») |
| `AI_FLOOR_DEMO_EPOCH` | instante UTC en que se pone el enrutado a 1, ISO 8601 con offset, p. ej. `2026-10-25T20:00:00Z` | con el enrutado | época nueva: el dashboard y «Día N de 30» cuentan solo desde aquí |
| `AI_FLOOR_DEMO_START_EQUITY` | NAV de la cuenta practice leída en el pre-flight 4.6 (decimal, p. ej. `100012.34`) | con el enrutado | base del gate PAPER de drawdown 5 % (DEC-DEMO-GATE-1); sin ella queda la base del 18-oct |
| `AI_FLOOR_DAILY_LOSS_START_EQUITY` | el mismo NAV | solo si `AI_FLOOR_DAILY_LOSS_LIMIT=1` ya está puesto | base del freno de drawdown 5 % del router (si no, sigue el `dd_start` guardado de la época anterior) |

No se cambia ninguna otra clave. En particular **no** poner: `V2_OPPOSITE_SETUP_POLICY`, `AI_FLOOR_FX_SL_MIN_PIPS`,
`AI_FLOOR_FX_SL_MAX_PIPS`, `AI_FLOOR_FX_TP_RR`, `AI_FLOOR_MAX_HOLD_TRADING_DAYS`, `AI_FLOOR_FRIDAY_FLATTEN_NY`,
`AI_FLOOR_TIME_EXIT_DAY1`, `AI_FLOOR_DEMO_REVERSAL` (los defaults del código son los valores pedidos), ni tocar
`AI_FLOOR_GIT_COMMIT` (identifica el freeze del experimento; si cambia, `experiment_resumable` falla y el servicio no
arranca), `AI_FLOOR_ENABLED_SYMBOLS` (sigue `XAUUSD,EURUSD`: la pausa de XAUUSD es por código), ni secretos.

Recuento esperado tras el paso 3: **recuento previo + nº de claves que no existían** (cambiar el valor de una clave
existente no suma). `AI_FLOOR_SCHEDULER`, `AI_FLOOR_CLOUD_RUNNER` y `AI_FLOOR_BROKER_DEMO_ROUTING` ya existen en `0`
(el GET previo debe mostrarlos así). `AI_FLOOR_RESULTS_SOURCE` es nueva (+1). Según DEC-DEMO-GATE-1
`AI_FLOOR_DEMO_START_EQUITY` no estaba fijada en producción (+1); `AI_FLOOR_DEMO_EPOCH` y
`AI_FLOOR_DAILY_LOSS_START_EQUITY`: +1 cada una solo si el GET previo no las muestra.

| Escenario | Claves que se ponen | Nuevas | Recuento (desde 38) |
|---|---|---|---|
| Sin decisión del paso 0 | `AI_FLOOR_RESULTS_SOURCE` | 1 | **39** |
| Con decisión, `AI_FLOOR_DEMO_EPOCH` ya existía | las 6 de la sección 0 | 2 (`RESULTS_SOURCE`, `DEMO_START_EQUITY`) | **40** (41 con `DAILY_LOSS_START_EQUITY` nueva) |
| Con decisión, `AI_FLOOR_DEMO_EPOCH` no existía | las 6 de la sección 0 | 3 | **41** (42 con `DAILY_LOSS_START_EQUITY` nueva) |
| **Domingo 2026-10-25 (verificado)**: DEC-DEMO-RESUME-1, `DEMO_EPOCH` no existe, `DAILY_LOSS_LIMIT=1` | las 7 (incluida `DAILY_LOSS_START_EQUITY`) | 4 | **42** |

Cualquier otra diferencia ⇒ parar y revisar.

## 3. Despliegue

1. El owner revisa y mergea el PR de `feat/fx-sl-week-hold` a `main` (CI `Tests / unit` en verde). Anotar `<MERGE_SHA>`.
2. Archivado (sección 5) **antes** del deploy.
3. Aplicar los PUT por clave de la sección 2 según el escenario: sin decisión del paso 0 **solo**
   `AI_FLOOR_RESULTS_SOURCE`; con ella, las siete claves (los 7 PUT de la sección 2 del escenario del domingo; scheduler, runner y enrutado
   juntos, nunca uno sin los otros).
   `autoDeployTrigger` sigue `off`: los PUT no despliegan.
4. `POST https://api.render.com/v1/services/<SERVICE_ID>/deploys` con `{"commitId": "<MERGE_SHA>"}` (nunca otro SHA).
5. Esperar `status = live` (`GET /v1/services/<SERVICE_ID>/deploys/<DEPLOY_ID>`); si `build_failed` /
   `update_failed` ⇒ rollback (sección 6).
6. Ejecutar los checks post-deploy de la sección 4 (marcados «post»).

## 4. Pre-flight y post-checks

| # | Check | Cuándo | Cómo / esperado |
|---|---|---|---|
| 4.1 | Health | pre y post | `GET <SERVICE_URL>/_stcore/health` ⇒ 200 |
| 4.2 | Cabeceras de seguridad | post | `curl -sI <SERVICE_URL>/` ⇒ `strict-transport-security`, `content-security-policy` (con `frame-ancestors 'none'`), `x-frame-options: DENY`, `x-content-type-options: nosniff`, `referrer-policy: no-referrer` |
| 4.3 | Recuento de env | pre y post | tabla de la sección 2 (38 antes, verificado; domingo con DEC-DEMO-RESUME-1: **42** después; 39 solo en el escenario genérico sin decisión) |
| 4.4 | Flags | post | env: `AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`; con decisión: `AI_FLOOR_SCHEDULER=1`, `AI_FLOOR_CLOUD_RUNNER=1`, `AI_FLOOR_BROKER_DEMO_ROUTING=1`, `AI_FLOOR_DEMO_EPOCH` / `AI_FLOOR_DEMO_START_EQUITY` / `AI_FLOOR_DAILY_LOSS_START_EQUITY` (incluida: `AI_FLOOR_DAILY_LOSS_LIMIT=1`) con los valores anotados; sin decisión: los tres primeros en `0`. `V2_OPPOSITE_SETUP_POLICY` sigue en `STRUCTURE_CHANGE` (sin cambios). Logs (solo con decisión): `component=broker_demo event=ROUTER_READY dry_run=False`, `event=REVERSAL_ON`, `component=risk event=PAPER_DRAWDOWN_GATE source=OANDA_DEMO` |
| 4.4b | Solo resultados OANDA | post | dashboard (Resumen, Operaciones, Command Center) y Floor: ninguna cifra «PAPER» (nota «ledger PAPER es interno» en el Command Center); el resumen diario de Slack sale como `OANDA DEMO DAILY_SUMMARY` |
| 4.4c | «Cerrar operación» | post | Floor con sesión del owner: el botón aparece desactivado («No hay ninguna operación enrutada abierta») mientras no haya trade enrutado; **no** pulsarlo como prueba con una operación real del demo salvo decisión del owner |
| 4.5 | REAL bloqueado | post | código: `runtime/demo_runner.py` `REAL_EXECUTION_ENABLED = False` en `<MERGE_SHA>`; el router no se construye si no es False; el host es el de practice (`api-fxpractice.oanda.com`) |
| 4.6 | OANDA solo practice + NAV | pre | Dashboard vista Real / summary de solo lectura: cuenta practice, NAV (anotar para la sección 2), sin órdenes pendientes. **Domingo (DEC-DEMO-RESUME-1):** el owner reinició la cuenta OANDA practice a 100k ⇒ el NAV leído debe ser ≈ 100 000 (p. ej. 100 000,00x) **antes** de los PUT; si no es ≈ 100k ⇒ parar y preguntar al owner |
| 4.7 | Operaciones abiertas | pre | **Domingo (DEC-DEMO-RESUME-1):** la cuenta practice debe estar **plana**: `openTrades`, `openPositions` y `pendingOrders` vacíos; si no ⇒ parar y decidir con el owner. Escenarios genérico/otros: OANDA practice: `openTrades` vacío, o cada trade abierto identificado. Si hay un trade enrutado abierto, desde el deploy lo gestiona la regla nueva (cierre viernes 16:45 ET; sus SL/TP en el broker no cambian). Un trade **no enrutado** abierto en la cuenta ⇒ el router marca `DRIFT` y rechaza entradas: parar y decidir con el owner (el sistema nunca lo cierra). PAPER: posiciones EURUSD abiertas (planes 3R antiguos) siguen con su SL/TP; la regla de la semana las cierra como muy tarde el viernes 16:45 ET |
| 4.8 | Trade 13 intacto | pre y post | Anotar estado, precio, SL/TP y P&L del trade 13 en OANDA practice (y su fila en `broker_demo_trades` si existe) antes y después: deben ser idénticos. Ningún cambio de esta rama cierra, modifica ni borra un trade existente; solo actúa sobre trades enrutados `OPEN` por la regla de duración/reversión. Si el trade 13 está **abierto**, aplica 4.7 (no enrutado ⇒ DRIFT; enrutado ⇒ gestionado por la regla nueva): decisión del owner antes del deploy |
| 4.9 | Scheduler solo EURUSD | post (lunes ≥ 03:00 ET) | logs: `component=scheduler event=SYMBOL_PAUSED symbol=XAUUSD` (una vez), `RUN_COMPLETED` solo con `symbol=EURUSD` |
| 4.10 | XAUUSD pausado | post | sin ciclos ni órdenes XAUUSD; posición PAPER XAUUSD (si existiera) cerrada con `SYMBOL_PAUSED` |
| 4.11 | Latches del router | pre | dashboard «Daily loss limit»: sin `DRAWDOWN_HALT` latcheado de la época anterior (si lo hay: `clear_drawdown_halt` por el operador, con motivo) |
| 4.12 | Slack | post | `python -m runtime.alert_test --slack` (mensaje TEST) opcional; el lunes tras las 17:00 ET: log `component=notifications event=DAILY_SUMMARY_SENT source=OANDA_DEMO` y el resumen del día de trading |
| 4.13 | Preflight cloud | post | el servicio arrancó: health 200 (con decisión: preflight de activación con `experiment_resumable`, y heartbeat del runner en el dashboard; sin ella: `infra_ready` con `scheduler_not_started`) |
| 4.14 | Scheduler / runner | post (lunes ≥ 03:00 ET) | con decisión: heartbeat del scheduler avanzando en el dashboard (Sistema), el primer `RUN_COMPLETED symbol=EURUSD` en la sesión de Londres, gasto de IA de nuevo activo (esperado). Sin decisión: **ningún** `RUN_COMPLETED` ni gasto de IA |

## 5. Reinicio del reloj de 30 días (época nueva)

1. **Archivar, nunca borrar.** Desde el shell de Render, antes del deploy, copias consistentes (API de backup de SQLite,
   origen en solo lectura) de la DB de trading (`$AI_FLOOR_DB_PATH`) y de la DB de estado del broker
   (`$AI_FLOOR_BROKER_STATE_DB_PATH`, por defecto `data/runtime/broker_state.db`) a
   `data/runtime/archive/<nombre>_pre_restart_20261025.db` (mismo disco persistente). Por ejemplo:
   `python -c "import sqlite3,sys; s=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" <origen> <destino>`.
   No sobrescribir un destino existente. Comprobar `PRAGMA integrity_check` = `ok` en cada copia. Opcional: copia fuera
   de Render.
2. Las DB activas **se conservan** (no se renombran ni se vacían): la experiencia PAPER, el journal y las filas
   enrutadas antiguas siguen ahí; el fingerprint no cambia, así que el preflight reanuda el experimento PAPER.
3. La época nueva la fija `AI_FLOOR_DEMO_EPOCH` (sección 2): el dashboard muestra solo operaciones OANDA desde ese
   instante y «Día N de 30» cuenta desde la primera operación enrutada tras él. Base de drawdown =
   `AI_FLOOR_DEMO_START_EQUITY` (y `AI_FLOOR_DAILY_LOSS_START_EQUITY` si el límite diario está ON).
4. Día 30 ≈ 2026-11-24 si la primera operación entra el 2026-10-26. Registrar en `docs/STATUS.md` la época real.

## 6. Rollback

1. Si el problema es solo del enrutado: `PUT .../env-vars/AI_FLOOR_BROKER_DEMO_ROUTING` = `0` (por clave). Con trades
   enrutados abiertos, antes cerrarlos con «Cerrar operación» (Floor, uno) o el botón de emergencia del dashboard
   (flatten, todos) y confirmar `openTrades` vacío. Sin enrutado no hay ciclos solo-PAPER: poner también
   `AI_FLOOR_SCHEDULER=0` y `AI_FLOOR_CLOUD_RUNNER=0` (por clave, ambos; como el 2026-10-22).
2. Si hay que volver al código anterior: `POST /v1/services/<SERVICE_ID>/deploys` con `{"commitId": "<SHA live
   anterior>"}` (anotar el SHA live antes del paso 3.4; según DEC-DEMO-PAUSE-1 se redesplegó el `main` de entonces, `0b443b0`:
   verificarlo en Render). Hacerlo con la cuenta plana: el código
   anterior aplicaría la regla Day-1/Day-2 a trades abiertos y no reconoce la política de riesgo FX sellada en órdenes
   PAPER pendientes (se rechazarían al rellenarse, fail-closed).
3. Env: devolver las claves de la sección 2 a su estado previo (enrutado `0`; las claves nuevas se pueden eliminar con
   `DELETE .../env-vars/<KEY>`, por clave; `AI_FLOOR_RESULTS_SOURCE` se puede borrar sin efecto en el fingerprint) y
   comprobar el recuento (38). Scheduler y runner: ambos `0` (nunca uno solo, ver sección 1).
4. Datos: nada que restaurar (no se borró nada); las copias de la sección 5 quedan como evidencia.
5. Registrar el rollback en `CHANGELOG_AGENT.md` y `docs/STATUS.md`.
