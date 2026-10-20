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
ON (`AI_FLOOR_BROKER_DEMO_ROUTING=1`), la vista OANDA demo de solo lectura ya existente (`luxury.demo_api()`: lecturas
GET cacheadas). `payload["source"]` = `OANDA_DEMO` | `PAPER` | `None`. En modo demo nunca se muestran cifras PAPER
en la cabecera ni en las operaciones.

| Campo | Real (fuente) | Si no hay dato |
|---|---|---|
| `header.equity` | Demo: NAV OANDA. PAPER: `paper_accounts.equity` (nunca el id de cuenta). | `—` |
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
| `agents.*.signals` / `hit_rate` | Últimas 5 decisiones direccionales del agente (BULLISH/BEARISH) con el resultado de la operación del **mismo run_id**; acierto = el sesgo coincide con el lado y ganó (o lo contradice y perdió). | `—` si ninguna señal enlaza con una operación cerrada |
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

## Tests

`test_floor_view.py`: DB inexistente/vacía ⇒ todo `None`; errores de lectura ⇒ `None`; filas sembradas ⇒ valores reales
(PAPER y demo con fake, sin red); modo muestra solo rellena lo que falta; escape del JSON; el asset no contiene cifras
del ejemplo ni `fetch`/URLs; sin id de cuenta ni secretos; `app.py` registra la página; sin imports de rutas de órdenes
(AST + subproceso); la página se renderiza en la app real (AppTest) en ambos modos sin escribir la DB.
