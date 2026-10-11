# Fase 13: runbook del demo PAPER de 30 días en Render

**Estado:** preparado, NO ejecutado. Hace falta el **GO explícito del owner** (Jeferson). Ningún agente toca Render, los flags, el scheduler ni el deploy sin ese GO.
**Invariantes:** solo PAPER · `REAL_EXECUTION_ENABLED = False` (constante en código, no se puede cambiar por env) · NAS100 bloqueado en código · una sola instancia.

## 1. Pre-flight (antes de desplegar)
- [ ] El commit de main a desplegar tiene CI en verde; anotar el SHA y la URL del run.
- [ ] Fases 0-12 CERTIFIED en `docs/STATUS.md`.
- [ ] Decisiones del owner registradas en `docs/decisions.md`: GO de la Fase 13, flag de catch-up (sección 3) y baseline A2 (cuenta PAPER nueva de USD 10 000, DB nueva, DEC-8.20).
- [ ] Secretos cargados en Render (los configura el owner, nunca se pegan en el chat ni en el repo):
  - `OPENAI_API_KEY`: proveedor de IA (`AI_FLOOR_AI_PROVIDER=openai`, modelo `OPENAI_MODEL` en `render.yaml`).
  - `TWELVE_DATA_API_KEY`: datos de mercado de XAUUSD y EURUSD en 5 min.
  - `AI_FLOOR_DASHBOARD_PASSWORD`: protege el Command Center.
  - `SLACK_WEBHOOK_URL`: alertas de Slack.
  - `GMAIL_APP_PASSWORD`: alertas por email (el remitente y el destinatario son yefersontejeda@gmail.com). **No está en `render.yaml`**: el owner debe añadirlo en el panel de Render.
  - Opcionales y sin uso por defecto: `MASSIVE_API_KEY`, `FINNHUB_API_KEY`, `FXMACRODATA_API_KEY` (macro = `none`).
- [ ] El disco persistente `paper-state` (1 GB) está montado en `data/runtime`, y hay que empezar con una DB nueva para el baseline A2.
- [ ] Backup: guardar fuera de Render una copia de la DB anterior, si existe.

## 2. Flags (env) para el demo, y por qué
| Variable | Valor demo | Motivo |
|---|---|---|
| `V2_PLANNER_POLICY` | `V2_F3` | DEC-14.2 (owner Jeferson Tejeda, 2026-10-11 11:33 ET): plan 3.00R, fill PAPER ≥ 2.50R por spread, latencia y movimiento del precio. Default V1 hasta el demo; independiente de `V2_OPPOSITE_SETUP_POLICY`. |
| `AI_FLOOR_ENABLED_SYMBOLS` | `XAUUSD,EURUSD` | Mercados del experimento. NAS100 está bloqueado en código. |
| `AI_FLOOR_MARKET_PROVIDER` | `twelve_data` | Datos reales; los proveedores descartan las velas sin cerrar (ver ISSUE-012). |
| `AI_FLOOR_AI_PROVIDER` | `openai` | Usa la IA real; si falla, la política de revisión deja la ejecución en estado no disponible (fail-closed). |
| `AI_FLOOR_MACRO_PROVIDER` | `none` | Sin proveedor macro aprobado. |
| `AI_FLOOR_CLOUD_RUNNER` | `1` | Arranca el runner en la nube (hoy está en `0`). |
| `AI_FLOOR_SCHEDULER` | `1` | Ciclos automáticos de 15 min (hoy está en `0`). |
| `AI_FLOOR_ALERTS_ENABLED` | `1` | Activa email y Slack en el worker aislado; un fallo de alerta nunca cambia PAPER. |
| `AI_FLOOR_ALERTS_LEDGER_PATH` | `data/runtime/alerts.db` | Ledger durable en el disco persistente, para la recuperación de envíos atascados al reiniciar. |
| `AI_FLOOR_INSTANCE_COUNT` | `1` | Un solo escritor. |
| `AI_FLOOR_V2_REX` | `1` (recomendado) | Evidencia REX en el journal, solo de observación; no cambia la economía (lo comprueba el test E2E de la Fase 12). |
| `AI_FLOOR_V2_RHALT` | `1` (recomendado, requiere REX) | Puerta de halt/recovery (M-5). |
| `AI_FLOOR_V2_SEALED_GENESIS` | `1` (recomendado con DB nueva, requiere REX) | Sella el genesis del baseline A2. |
| `AI_FLOOR_V2_POSITION_CATCH_UP` | **decisión del owner** (sección 3) | |

## 3. Decisión: `AI_FLOOR_V2_POSITION_CATCH_UP`
- **Qué hace:** guarda en `market_evidence` las velas 5m cerradas (en un SQLite separado, `AI_FLOOR_MARKET_EVIDENCE_PATH`) y gestiona SL/TP de las posiciones **en orden cronológico vela a vela**, incluidas las que el sistema se perdió. Así se corrige HIGH-8.1 (antes, ponerse al día tras un hueco podía saltarse un SL o un TP).
- **Si ON:**
  - Los gráficos de las alertas tienen datos (PNG).
  - SL y TP se evalúan sobre todas las velas.
  - Requiere `AI_FLOOR_MARKET_EVIDENCE_PATH` en un archivo distinto de la DB principal, y `AI_FLOOR_V2_POSITION_CATCH_UP_SYMBOLS=XAUUSD,EURUSD`.
  - **Cambia el comportamiento de trading** respecto al camino legado: los cierres pueden ocurrir en otra vela o a otro precio. Por eso el PnL no es comparable con el baseline anterior (ya se exige un baseline A2 nuevo).
- **Si OFF:** camino legado con la vela más reciente (riesgo HIGH-8.1 en los huecos) y gráficos NO_DATA.
- **Recomendación de Grok Bot:** **ON** desde el día 1 con la DB nueva, para que el demo mida el comportamiento corregido. La decisión es del owner.

## 4. Despliegue (lo ejecuta el owner, o un agente solo con GO explícito)
1. En Render: cargar los secretos y los flags de las secciones 1 y 2 (`autoDeployTrigger` sigue en `off`).
2. Hacer un Manual Deploy del SHA anotado. El build es `pip install -r requirements-ci.txt` (incluye matplotlib).
3. Comprobar que el health check `/_stcore/health` responde 200.

## 5. Smoke checks post-deploy (día 0)
- [ ] **F09-T18:** desde el shell de Render, `python -m runtime.alert_test --email` y `--slack`. Tienen que llegar un email y un mensaje marcados TEST.
- [ ] **F10:** abrir el dashboard en iPhone y en escritorio, comprobar que pide la contraseña y revisar las páginas Overview, Positions, Setups, Decisions, Health, Alerts, History y Errors. Guardar capturas en el PR o issue de la Fase 13.
- [ ] El primer ciclo programado termina COMPLETED para los dos símbolos; el journal tiene RUN_STARTED…RUN_COMPLETED (y REX_RUN si REX está ON).
- [ ] `python -m replay.status_verifier` sobre una copia de la DB da SQLITE_CHECKS_PASS.

## 6. Monitorización diaria (días 1-30)
- Ejecutar el status verifier sobre una copia de la DB (cadena EDG/REX, G14, conteos).
- Dashboard: equity, PnL realizado y no realizado, posiciones abiertas, errores, salud de los proveedores y del scheduler.
- Alertas: revisar la página ALERTS por FAILED o AMBIGUOUS_RETRY.
- Si hay halt o incidente: no reiniciar a mano el estado PAPER. Abrir un issue con el run_id y seguir la recuperación (R-HALT y M-5).
- Guardar cada día una copia de la DB fuera de Render.

## 7. Informe del día 30
- Ciclos ejecutados y ciclos perdidos (SLOTS_MISSED), con su disponibilidad.
- Operaciones por símbolo: win rate, PnL neto, drawdown máximo y R medio.
- Rechazos del riesgo y de la IA por motivo, y fallos de proveedores.
- Integridad: resultado del verifier, cadena REX y halts.
- Alertas: entregadas, fallidas y ambiguas.
- Recomendación de GO / NO-GO para la siguiente etapa. REAL sigue **prohibido** salvo una fase nueva con su propia aprobación.

DEC-14.2: el oráculo REX actual verifica V1 únicamente. F3 registra la política efectiva y el verifier rechaza `UNSUPPORTED_RR_POLICY`; no interpretar esa ejecución como verificada por ese oráculo.
