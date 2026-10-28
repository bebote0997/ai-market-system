# Página "Floor" (vista animada del trading floor) — solo lectura

Página nueva del dashboard (`Floor`, en la navegación bajo "Vistas detalladas") que muestra el floor isométrico
animado del mockup aprobado v4 (8 agentes, mesa EURUSD/XAUUSD, 8 pasos del ciclo, panel de operación, fichas de agente,
zoom con rueda/pellizco, layout móvil). **No ejecuta, no escribe y no envía órdenes.** Queda detrás del mismo login
(`require_dashboard_access`) que el resto del dashboard.

## Archivos

| Archivo | Qué hace |
|---|---|
| `ui/static/floor/floor.html` | Asset estático (HTML/CSS/JS canvas). No contiene datos ni cifras: lee el JSON inyectado en `<script id="floor-data" type="application/json">`. No hace `fetch` ni carga recursos externos. |
| `ui/floor_payload.py` | `build_floor_payload(...) -> dict`: lecturas puras; cada sección va envuelta en un guard (error de lectura ⇒ `None`, nunca excepción). |
| `ui/views/floor_view.py` | Construye el payload, lo inyecta (JSON con todo `<` escapado como `<`, así no puede aparecer `</script>`) y lo muestra con `st.iframe` (iframe `srcdoc` en sandbox, alto 860 px). Usa `streamlit.components.v1.html` solo si la versión de Streamlit no tiene `st.iframe`. |
| `ui/fixtures/floor_example.py` | Datos de ejemplo del prototipo. **Solo en modo muestra.** |
| `ui/app.py` | Registra la página (`floor_view.PAGE == "Floor"`) y llama a `demo_refresher.ensure_started()` (idempotente). |
| `ui/demo_refresher.py` | Productor en segundo plano de las cachés demo que el Floor lee (ver «Datos siempre calientes»). |
| `test_floor_view.py` | Tests (ver abajo). |

## De dónde sale cada campo

Fuentes: la DB PAPER (`AI_FLOOR_DB_PATH`, abierta `mode=ro` vía `ui.state.dashboard_database`) con los mismos helpers
que el dashboard (`ui.views.luxury`, `ui.views.process`, `ui.views.dashboard.records/payloads`); con el routing demo
ON (`AI_FLOOR_BROKER_DEMO_ROUTING=1`), la vista OANDA demo de solo lectura ya existente (`luxury.demo_api()`) alimentada SOLO con lo que ya contienen las
cachés del dashboard (`ui.floor_cache`: lectura "peek" de `execution.oanda_readonly._TRADES/_VIEWS`, sin refrescar).
El Floor nunca dispara una llamada al broker ni a la red: si no hay nada en caché, los campos demo son `None` ("—"),
o «cargando…» mientras el productor (`ui/demo_refresher.py`) está llenando las cachés tras el arranque del proceso.
`payload["source"]` = `OANDA_DEMO` | `PAPER` | `None`. En modo demo nunca se muestran cifras PAPER en la cabecera, en
las operaciones ni en los resultados de las señales de los analistas.

**Solo desde el epoch (modo demo).** El epoch sale de `demo['epoch']()` (`AI_FLOOR_DEMO_EPOCH`). Snapshot
(`ui_snapshots.as_of`), runs (`as_of`/`started_at`), `agent_decisions.as_of`, la evidencia del ciclo (del run filtrado),
el feed (`journal.timestamp`) y las líneas de mercado solo se usan si su hora es ≥ epoch; hora ausente o ilegible ⇒
fuera; epoch ilegible ⇒ nada. Los ciclos PAPER antiguos siguen en la DB pero nunca se ven. Si no queda nada:
`waiting=True`, `waiting_text="Esperando el primer ciclo"`, `epoch_text` ("25 oct 11:30 ET"). En modo PAPER se usa la
DB completa.

**Ciclo en marcha.** `running = {active, step, symbol, started_at_text}`: activo SOLO si `runs` tiene una fila
`status='RUNNING'` (la escribe `storage.database.Store` mientras corre un ciclo) de EURUSD/XAUUSD, iniciada ≥ epoch y
hace ≤ 15 min (una fila RUNNING más vieja es un proceso caído). `step` = índice (0..7) del primer paso del Floor sin
evidencia persistida (misma lógica que `cycle` / `process.cycle_evidence`); `None` si no se sabe.

**Nota "datos reales".** `note_real` solo si la lectura de NAV en caché o el último ciclo tiene < 15 min;
`cycle_text` = "último ciclo 26 oct 03:00 ET" (+ " (antiguo)" si es más viejo). Nunca "datos reales" sobre datos viejos
o previos al epoch.

| Campo | Real (fuente) | Si no hay dato |
|---|---|---|
| `header.equity` (+ `equity_stale`, `equity_as_of`) | Demo: NAV OANDA en caché; si es la última lectura buena (`stale`) o se observó hace más de 120 s (30 min con mercado FX cerrado y sin ciclo activo), se muestra un único aviso breve «dato de HH:MM ET» en la cabecera; sin hora de observación ⇒ `—`. PAPER: `paper_accounts.equity` (nunca el id de cuenta). | `—` |
| `header.pnl`, `win_rate`, `drawdown`, `trades` | Demo: `demo_statistics` desde el epoch. PAPER: `ledger_statistics` (ledger completo). | `—` |
| `header.r_total` | R medio × nº de operaciones cerradas (solo si el R de todas es conocido). | `—` |
| `header.drawdown_pct` | Demo: drawdown / capital de inicio del demo. PAPER: no disponible. | se muestra el drawdown en $ o `—` |
| `header.daily_loss_used_pct` / `limit_pct` | Demo: `daily_loss_view` (flags del router + NAV), límite = fracción configurada (2 %). PAPER: no aplica. | `—` |
| `header.day_text` | Demo: "Día N de 30" (`ui.demo_clock.demo_day_from_epoch` + `i18n_es.demo_day_text`; día 1 = fecha NY del epoch, sin tope; > 30 ⇒ "Día 30 de 30 – completo"). | `—` |
| `header.balance`, `today_pnl`, `open_positions`, `closed_trades` | Demo: balance OANDA en caché, `stats.today_pnl`, nº de filas abiertas, `stats.trades`. PAPER: solo `today_pnl` / `closed_trades` del ledger. | `—`; con 0 operaciones el Floor dice "sin operaciones aún" (tasa de acierto, R) |
| `header.daily_loss_text` | Estado del límite de pérdida diaria en español ("Bien", "Límite alcanzado · …"). | `—` |
| `trades[]` (EURUSD, abiertas + cerradas recientes, máx. 10) | Demo: filas OANDA; PAPER: `paper_positions` OPEN + `closed_trades`. Esperado (entrada/SL/TP/R:R) = plan persistido enlazado por identidad exacta (`luxury.linked_plan`; demo: `client_id aif-<run_id>`); riesgo esperado = `risk_decisions.capital_at_risk`; slippage = fill − entrada planeada (pips en EURUSD). | lista vacía ⇒ "sin datos aún" |
| `trades[].MAE/MFE` | No se guarda. | siempre `—` |
| `agents.Structure/Liquidity/Macro` (Mateo, Sofía, Andrés; antes Atlas, Nova, Orion) | Último `ui_snapshots` EURUSD; si falta, último `agent_decisions`. | `None` ⇒ "sin datos aún" |
| `agents.Risk` (Valentina, antes Vega) | El motor no tiene una IA "analista de riesgo": se muestra el **Trade Reviewer** (revisa el plan y su riesgo). Sin plan su estado es "No hacía falta". | idem |
| `agents.Reviewer` (Lucía, antes Sage) | Setup Reviewer. | idem |
| `agents.Conflict` (Diego, antes Arbiter) | Eventos de conflicto / política de setup opuesto del último ciclo (`process.cycle_evidence`). | idem |
| `agents.RiskEng` (Camila, antes Sentinel) | `risk_decisions` del último ciclo (estado, motivo, % riesgo, capital en riesgo); si falta, `risk_status` del snapshot. | idem |
| `agents.Exec` (Carlos, antes Echo) | Última operación EURUSD (abierta o cerrada); hit rate = % de cerradas ganadoras. | idem |
| `names` | Nombres visibles de los 8 personajes (`ui/agent_names.py`, DEC-AGENT-NAMES-1): `{id: {name, role, short}}`. Solo presentación; los ids internos (`agents.*`) no cambian. | sin `names` ⇒ el Floor muestra el id |
| `agents.*.signals` / `hit_rate` | Últimas 5 decisiones direccionales del agente (BULLISH/BEARISH) con el resultado de la operación del **mismo run_id** en la fuente seleccionada (demo: operaciones OANDA `aif-<run_id>`; nunca resultados PAPER en métricas demo; operaciones demo desconocidas ⇒ resultado y hit rate `None`); acierto = el sesgo coincide con el lado y ganó (o lo contradice y perdió). | `—` si ninguna señal enlaza con una operación cerrada |
| `cycle.steps[8]` | Último run EURUSD (demo: ≥ epoch): un paso está ✓ si tiene evidencia persistida (`process.cycle_evidence`); "Entrada / Salida" también si hay operación demo de ese run; "Resultado" si el run terminó. Hora = primer timestamp persistido del paso (nunca inferido). | sin marcas |
| `symbols` | `enabled_market_symbols()` + `runtime.symbol_pause.is_paused` (XAUUSD pausado, DEC-XAU-OFF-1). | — |
| `feed` | Últimos 5 eventos del `journal` (EURUSD/XAUUSD): hora ET, símbolo, tipo. | "sin datos aún" |
| `market.lines` | Estado/frescura/hora del último snapshot EURUSD (no hay precios de mercado persistidos para el floor). | "sin datos aún" |
| `curve` | Últimos 40 puntos del PnL realizado acumulado (misma curva del Resumen). | sin línea |

`payload["real_fields"]` lista los campos que vinieron de datos reales; `payload["example_fields"]` los rellenados con
el ejemplo.

## Modo muestra y "EJEMPLO"

- Modo muestra (`st.session_state["sample_mode"]`): **solo** las secciones sin dato real se rellenan con
  `ui/fixtures/floor_example.py` y se marcan con la etiqueta **EJEMPLO** (por KPI, panel de operación, ficha de agente,
  pantallas del muro, pasos).
- Fuera del modo muestra: solo datos reales o `—`; ninguna etiqueta EJEMPLO.
- **En reposo el Floor es estático**: sin `running.active` no hay bucle de pasos ni `requestAnimationFrame` continuo; se
  dibuja una vez y se redibuja a demanda (carga, resize, zoom/arrastre, panel). Personajes quietos en su mesa, sin
  partículas ni monitores animados; pasos en gris con ✓ + hora solo del ciclo real guardado. Con un ciclo en marcha se
  anima solo el paso alcanzado (`running.step`). La página se reconstruye en cada refresco de 60 s del fragmento de
  `ui/app.py`: un ciclo que empieza aparece en el siguiente refresco.
- Con una operación enrutada abierta solo Carlos (Exec, mesa EURUSD) la refleja: lado, R y P&L del payload (sin ticks
  inventados). XAUUSD pausado: "PAUSADO por el planificador".
- Personajes (DEC-AGENT-NAMES-1, solo presentación; `ui/agent_names.py`): Structure = Mateo · Estructura, Liquidity = Sofía · Liquidez,
  Macro = Andrés · Macro, Risk = Valentina · Riesgo, Reviewer = Lucía · Revisor, Conflict = Diego · Conflictos,
  RiskEng = Camila · Motor de riesgo, Exec = Carlos · Mesa EURUSD. Escritorio/pared: placa «NOMBRE · ROL»; móvil
  (< 600 px) o si esa placa se solaparía con otra: solo el nombre (el rol está en la ficha y en la cadena de decisión).
  El bloque de cierre manual se titula con el nombre de Exec («Carlos · Gestor de la operación · mesa de riesgo»).
- `?step=N` (capturas/tests) fija un paso; `?agent=<id>` abre la ficha; `?detail` abre el panel de operación.
- Textos ≥ 10 px en el canvas; códigos del motor traducidos o humanizados (`floor_payload.code_text`).
- HUD: las celdas pasan a una segunda fila en vez de solaparse o cortarse (360–1600 px); con 0 operaciones R total y
  tasa de acierto dicen "sin operaciones" en gris, en dos líneas. "Balance $x" bajo el NAV (HUD y muro) solo si difiere
  del NAV.
- Escenario estrecho (< 600 px, clase `body.mob`): la escena se ajusta al recuadro de salas y pantallas (no a las
  esquinas vacías del suelo); sin texto en las pantallas del muro ni nombres de sala; los recuadros EURUSD/XAUUSD se
  convierten en una tira HTML bajo el canvas (EURUSD abre el panel si está activo). Las placas de nombre que se
  solaparían con otro recuadro de texto se omiten: nunca dos textos encima.

## Datos siempre calientes: productor de cachés (`ui/demo_refresher.py`)

**Problema.** El Floor solo lee («peek») las cachés TTL de `execution.oanda_readonly` (`_TRADES[epoch]`,
`_VIEWS["practice"]`) y solo las otras páginas (Resumen/Operaciones/Setups/experimento vía `demo_snapshot`) las llenaban.
Quien entraba directo al Floor (botón «Ver en el Floor» de Slack, móvil, pantalla de pared) veía «sin datos» / «dato
antiguo» (todo gris), y las otras páginas bloqueaban 5-8 s en una lectura OANDA síncrona cuando el TTL de 10 s vencía.

**Productor.** `ensure_started(env=None)` (lock de módulo + flag, idempotente) arranca UN hilo daemon en el proceso del
dashboard: en el arranque del servidor desde el `lifespan` de `st.App` (`ui/asgi_app.py`; verificado que el lifespan
corre en el mismo proceso y comparte `sys.modules` con el script) y otra vez, sin efecto si ya corre, desde `ui/app.py`
(cubre `streamlit run ui/app.py`). Solo arranca con resultados demo visibles (enrutado ON o
`AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`, la misma regla con la que el Floor lee las cachés), epoch conocido y credenciales
de lectura practice presentes (solo presencia; nunca se registran). Cada 2 s llama a los lectores existentes
`cached_demo_trades(epoch)` y `cached_account_view("practice")` con las MISMAS claves que el Floor lee. El ritmo de GETs
lo fija el TTL de 10 s de las cachés, no el bucle: 1 GET summary + GETs de trades (CLOSED + OPEN) por ventana de TTL,
≈3 GETs cada 10 s (el bucle corto solo acorta el momento tras el vencimiento en que un visitante refrescaría y esperaría).
Sin endpoints nuevos ni tipos de petición nuevos, sin escrituras, sin órdenes. Cada iteración va en try/except (solo se
registra el TIPO de excepción); tras 3 fallos seguidos espera 30 s (una vista `OK` con `stale=True`, la última buena
conservada tras un refresco fallido, cuenta como fallo). `stop()` para tests / apagado.

**Primer pintado.** Si el productor corre pero una caché aún está vacía (proceso recién arrancado), el payload marca
`header.equity_loading` / `header.trades_loading` (+ `loading`) y el Floor muestra «cargando…» en esas celdas y
«Cargando datos del demo…» en la nota (no «—» / «sin datos» / «dato antiguo»). Mientras `demo_refresher.warming_up()`,
el fragmento del Floor se re-ejecuta a los 8 s (`FIRST_RETRY`); cuando llegan los datos hace un `st.rerun()` y vuelve al
refresco de 60 s. Una lectura ya hecha que falló (auth/conexión: `CachedDemoTrades` guarda None; vista `ERROR`) no es
caché fría (`floor_cache.read_attempted`: hay entrada): muestra «sin datos» y refresco de 60 s, sin reintentos de 8 s.

**Lock de `CachedAccountViews`.** `get` ya no mantiene el lock durante el GET (mismo patrón que `CachedDemoTrades`): un
solo refresco en vuelo por entorno; los demás lectores reciben al instante la vista anterior; una primera lectura sin
nada en caché espera como máximo el timeout del GET (4 s) y si no hay resultado recibe una vista `ERROR` `timeout`. Se
mantiene TTL ≥ 5 s, `ERROR` conserva la última vista buena marcada `stale` y nunca lanza.

**Brillo (móvil / pantalla de pared).** Auditoría de contraste del asset: todos los colores de texto ≥ 4.5:1 sobre los
fondos oscuros (WCAG AA); lo que se veía apagado eran los datos ausentes/antiguos («—» en gris, «dato antiguo» en
ámbar) y el atenuado de Streamlit durante las lecturas bloqueantes de otras páginas. Corregido además: la pantalla NAV
decía «sin datos aún» bajo un NAV real cuando la curva tenía < 2 puntos; ahora «la curva aparece con 2 operaciones
cerradas».

## Seguridad

- Solo lecturas (`mode=ro`); el test de la app comprueba que el hash de la DB no cambia.
- Ningún import de rutas de órdenes/broker (`execution.*` no se importa; la vista demo llega vía `luxury.demo_api`,
  ya autorizada en `test_broker_wiring.py`).
- El payload no incluye id de cuenta, `client_id`, tokens ni contraseñas (test con secretos falsos en el entorno).
- CSP (`ui/security_headers.py`) sin cambios: el iframe `srcdoc` hereda la política del dashboard (`script-src
  'unsafe-inline'` ya permitido) y `frame-src 'none'` no lo bloquea. Verificado en Chrome headless con
  `streamlit run ui/asgi_app.py`: el script del floor corre y no hay errores CSP.

## «Cerrar operación» (DEC-MANUAL-CLOSE-1)

Debajo del floor, el gestor de la operación / mesa de riesgo ofrece **una** acción del owner: «Cerrar operación» sobre
la operación enrutada al demo OANDA (`ui/views/floor_actions.py`; `floor_view.py` sigue sin importar `execution.*`).

- Dos clics: «Cerrar operación» ⇒ «Confirmar cierre» (o «Cancelar»).
- Habilitado solo con sesión del owner (contraseña del panel, `ui.auth.owner_authenticated`; el modo loopback sin
  contraseña no cuenta), `AI_FLOOR_BROKER_DEMO_ROUTING=1`, cuenta de práctica OANDA (`101-`, proveedor `oanda`, host
  practice del cliente), `REAL_EXECUTION_ENABLED=False`, una fila enrutada OPEN sin acción pendiente y ningún cierre
  ya solicitado.
- El clic solo guarda `manual_close_request` en la DB de estado del broker
  (`execution.operator_actions.request_manual_close`, una transacción `set_flag_if_absent` + evento
  `MANUAL_CLOSE_REQUESTED`); el router lo ejecuta en segundos (el runner consulta las peticiones cada ~5 s entre ticks,
  `process_operator_requests`; también en cada ciclo/tick) con su propia ruta de cierre (`_close`, la
  misma del flatten de emergencia), motivo `MANUAL_CLOSE`, evento `MANUAL_CLOSE` (alerta Slack por la ruta existente
  `runtime/broker_alerts.py`). Rechazo definitivo del broker ⇒ `MANUAL_CLOSE_FAILED` (alerta) y la solicitud se
  descarta; cierre UNKNOWN ⇒ la solicitud espera a la reconciliación (nunca un segundo cierre). Dry-run ⇒
  `MANUAL_CLOSE_DRY_RUN`, nunca cierra.
- Idempotente: cada confirmación lleva un `request_id`; un segundo clic / petición concurrente no guarda otra
  solicitud (`duplicate` / `already_pending`).
- El motivo aparece en las operaciones cerradas del demo (Floor/Operaciones) como «Exit · cierre manual del owner».

## Tests

`test_floor_view.py`: DB inexistente/vacía ⇒ todo `None`; errores de lectura ⇒ `None`; filas sembradas ⇒ valores reales
(PAPER y demo con fake, sin red); modo muestra solo rellena lo que falta; escape del JSON; el asset no contiene cifras
del ejemplo ni `fetch`/URLs; sin id de cuenta ni secretos; `app.py` registra la página; sin imports de rutas de órdenes
(AST + subproceso); la página se renderiza en la app real (AppTest) en ambos modos sin escribir la DB; filtro por
epoch y "Esperando el primer ciclo"; ciclo en marcha (fresco ⇒ activo, 20 min ⇒ no, previo al epoch ⇒ no, paso
alcanzado); contador "Día N de 30"; sin bucle de animación fuera de `running`, sin `%8`, sin `AI_FLOOR_` ni códigos
crudos en el HTML.
`test_manual_close.py`: solicitud, rechazos (enrutado OFF, cuenta no práctica, REAL, sin operación, acción sin
resolver), idempotencia (doble clic y 6 hilos concurrentes), cierre del router (manage/on_cycle, rechazo, UNKNOWN,
ya cerrada, dry-run, solicitud corrupta), alerta Slack, motivo en español y flujo de dos clics con sesión del owner.

Polish B: KPI móviles en dos columnas; pasos en dos filas de cuatro y nota fuera del canvas para evitar solapamientos. El texto de los paneles se ajusta al ancho sin elipsis. Refresco del payload cada 60 s (si Auto-refresh está activo); sin TTL adicional ni caché de órdenes/acciones. Mercado FX cerrado: viernes 17:00 a domingo 17:00 NY. Un fallo explícito de lectura sigue marcado aunque sea reciente.

Productor de cachés (`test_demo_refresher.py`): arranca una sola vez (hilo daemon), no arranca sin resultados demo /
epoch / credenciales, llena las mismas claves que lee el Floor (payload con cifras frescas, `equity_stale` /
`trades_stale` False), sobrevive excepciones y aplica el backoff, no registra credenciales, estado «cargando» con
cachés frías, reintento a los 8 s, `lifespan`. `test_oanda_readonly.py`: el lock no se mantiene durante el GET.
