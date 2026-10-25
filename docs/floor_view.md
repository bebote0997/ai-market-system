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
| `ui/app.py` | Registra la página (`floor_view.PAGE == "Floor"`). |
| `test_floor_view.py` | Tests (ver abajo). |

## De dónde sale cada campo

Fuentes: la DB PAPER (`AI_FLOOR_DB_PATH`, abierta `mode=ro` vía `ui.state.dashboard_database`) con los mismos helpers
que el dashboard (`ui.views.luxury`, `ui.views.process`, `ui.views.dashboard.records/payloads`); con el routing demo
ON (`AI_FLOOR_BROKER_DEMO_ROUTING=1`), la vista OANDA demo de solo lectura ya existente (`luxury.demo_api()`) alimentada SOLO con lo que ya contienen las
cachés del dashboard (`ui.floor_cache`: lectura "peek" de `execution.oanda_readonly._TRADES/_VIEWS`, sin refrescar).
El Floor nunca dispara una llamada al broker ni a la red: si no hay nada en caché, los campos demo son `None` ("—").
`payload["source"]` = `OANDA_DEMO` | `PAPER` | `None`. En modo demo nunca se muestran cifras PAPER en la cabecera, en
las operaciones ni en los resultados de las señales de los analistas.

| Campo | Real (fuente) | Si no hay dato |
|---|---|---|
| `header.equity` (+ `equity_stale`, `equity_as_of`) | Demo: NAV OANDA en caché; si es la última lectura buena (`stale`) o se observó hace más de 120 s se muestra con "(dato antiguo · HH:MM ET)"; sin hora de observación ⇒ `—`. PAPER: `paper_accounts.equity` (nunca el id de cuenta). | `—` |
| `header.pnl`, `win_rate`, `drawdown`, `trades` | Demo: `demo_statistics` desde el epoch. PAPER: `ledger_statistics` (ledger completo). | `—` |
| `header.r_total` | R medio × nº de operaciones cerradas (solo si el R de todas es conocido). | `—` |
| `header.drawdown_pct` | Demo: drawdown / capital de inicio del demo. PAPER: no disponible. | se muestra el drawdown en $ o `—` |
| `header.daily_loss_used_pct` / `limit_pct` | Demo: `daily_loss_view` (flags del router + NAV), límite = fracción configurada (2 %). PAPER: no aplica. | `—` |
| `header.day_text` | Demo: "Día N de 30" (`demo_progress` + `i18n_es.demo_day_text`; día 1 = fecha NY de la primera operación enrutada). | `—` |
| `trades[]` (EURUSD, abiertas + cerradas recientes, máx. 10) | Demo: filas OANDA; PAPER: `paper_positions` OPEN + `closed_trades`. Esperado (entrada/SL/TP/R:R) = plan persistido enlazado por identidad exacta (`luxury.linked_plan`; demo: `client_id aif-<run_id>`); riesgo esperado = `risk_decisions.capital_at_risk`; slippage = fill − entrada planeada (pips en EURUSD). | lista vacía ⇒ "sin datos aún" |
| `trades[].MAE/MFE` | No se guarda. | siempre `—` |
| `agents.Structure/Liquidity/Macro` | Último `ui_snapshots` EURUSD; si falta, último `agent_decisions`. | `None` ⇒ "sin datos aún" |
| `agents.Risk` (Vega) | El motor no tiene una IA "analista de riesgo": se muestra el **Trade Reviewer** (revisa el plan y su riesgo). Sin plan su estado es "No hacía falta". | idem |
| `agents.Reviewer` (Sage) | Setup Reviewer. | idem |
| `agents.Conflict` (Arbiter) | Eventos de conflicto / política de setup opuesto del último ciclo (`process.cycle_evidence`). | idem |
| `agents.RiskEng` (Sentinel) | `risk_decisions` del último ciclo (estado, motivo, % riesgo, capital en riesgo); si falta, `risk_status` del snapshot. | idem |
| `agents.Exec` (Echo) | Última operación EURUSD (abierta o cerrada); hit rate = % de cerradas ganadoras. | idem |
| `agents.*.signals` / `hit_rate` | Últimas 5 decisiones direccionales del agente (BULLISH/BEARISH) con el resultado de la operación del **mismo run_id** en la fuente seleccionada (demo: operaciones OANDA `aif-<run_id>`; nunca resultados PAPER en métricas demo; operaciones demo desconocidas ⇒ resultado y hit rate `None`); acierto = el sesgo coincide con el lado y ganó (o lo contradice y perdió). | `—` si ninguna señal enlaza con una operación cerrada |
| `cycle.steps[8]` | Último run EURUSD: un paso está ✓ si tiene evidencia persistida (`process.cycle_evidence`); "Entry / Exit" también si hay operación demo de ese run; "Resultado" si el run terminó. Hora = primer timestamp persistido del paso (nunca inferido). | sin marcas |
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
- Las animaciones (personajes que caminan, el bucle de los 8 pasos, monitores) son decorativas; los pasos con ✓ y hora
  son los completados en el último ciclo real.

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
  `MANUAL_CLOSE_REQUESTED`); el router lo ejecuta en su próximo ciclo/tick con su propia ruta de cierre (`_close`, la
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
(AST + subproceso); la página se renderiza en la app real (AppTest) en ambos modos sin escribir la DB.
`test_manual_close.py`: solicitud, rechazos (enrutado OFF, cuenta no práctica, REAL, sin operación, acción sin
resolver), idempotencia (doble clic y 6 hilos concurrentes), cierre del router (manage/on_cycle, rechazo, UNKNOWN,
ya cerrada, dry-run, solicitud corrupta), alerta Slack, motivo en español y flujo de dos clics con sesión del owner.
