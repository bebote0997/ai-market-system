# EXPERIMENT_FROZEN — baseline oficial PAPER de 14 días

Fecha de freeze: **2026-09-19 UTC**.

- Baseline de código y economía: **`f5032baeb87766ad74905093c1b4195117995092`**.
- Base anterior auditada: `f9ae6ce79deb9baaa0dab0faa0271e462599d843`.
- Commit de freeze: el commit que introduce este documento; identificable mediante
  `git log --diff-filter=A --format=%H -- EXPERIMENT_FREEZE.md`. Su padre es el
  baseline anterior. Este commit solo añade documentación y reglas de freeze;
  no cambia código ejecutable respecto al baseline.
- Estado: **FROZEN / NOT_STARTED / RUNNER_OFF / SCHEDULER_OFF**.
- Inicio y fin oficiales de los 14 días: **no establecidos**. El freeze no inicia
  el contador, no crea una automatización y no constituye autorización para operar.

## Configuración congelada

| Parámetro | Valor |
| --- | --- |
| Ejecución | PAPER únicamente; REAL_EXECUTION_ENABLED=False |
| Símbolos | XAUUSD, EURUSD; NAS100 no habilitado |
| Sesiones | LONDON, NEW_YORK; lunes-viernes 08:00 <= hora local < 17:00, ZoneInfo/DST |
| Cadencia | 15 minutos |
| Timeframes | 1h contexto, 15m setup, 5m ejecución |
| Mercado | Twelve Data, modo twelve_data |
| Macro | FXMacroData, modo fxmacrodata |
| IA | OpenAI, modelo gpt-5.6-terra; prompts/agentes fijados por el SHA baseline |
| Frescura máxima | 1h: 7200s; 15m: 1800s; 5m: **600s** |
| Protección temporal | Corte de evidencia por slot/as_of; validación adicional del reloj real y sesión antes de ejecutar y después de IA |
| Riesgo previsto por operación | <=1% de equity; control post-fill conservado |
| R:R mínimo | 1:3; control post-fill conservado |
| Cuenta | paper-main, USD |
| Equity inicial | **10000 USD** |
| Comisión adicional | **0** |
| Spread adicional | **0** |
| Financiación/swap | **0** |
| Fill | **Open de la siguiente barra elegible procesada** |
| Gaps SL/TP | Política existente, sin cambios; stop-first intrabar conservado |
| Capital utilizado en sizing | Máximo 100% de equity, según configuración existente |
| XAUUSD | Cantidad en troy ounces; multiplier=1.0; incremento cantidad=0.001 oz; precio de referencia=0.01 USD/oz |
| EURUSD | Cantidad en EUR base; multiplier=1.0; incremento cantidad=1 EUR; precio de referencia=0.00001 USD/EUR |
| Redondeo | Cantidad hacia abajo; no se modifican precios observados ni entrada/SL/TP; sin conversión a lots |
| Persistencia | SQLite schema 3, disco montado en /opt/render/project/src/data/runtime |
| Archivo SQLite | /opt/render/project/src/data/runtime/trading_floor.db |
| Autoridad | Una instancia; flock exclusivo antes de crear runner y recuperar runs interrumpidos |
| Notificaciones | Slack; persistencia previa; intento durable at-most-once |
| DAILY_SUMMARY | Día UTC anterior completo; estado/PnL no realizado/equity como última instantánea persistida |
| Activación actual | AI_FLOOR_CLOUD_RUNNER=0 y AI_FLOOR_SCHEDULER=0 |

El usuario aprobó expresamente las unidades, incrementos, redondeo y modelo
económico anteriores. Aplican solo a este experimento PAPER. Semántica completa
en `PAPER_EXECUTION_SPEC.md`; hallazgos y pruebas en `AUDIT_FREEZE.md`.
No se almacenan secretos ni valores de credenciales en el freeze.

## Regla oficial de cambios desde este punto

**Solo se permiten fixes técnicos de defectos que invaliden el experimento o su
integridad.** Deben documentar reproducción, impacto, cambio mínimo y tests.
No se permite optimizar a partir de resultados, cambiar estrategia, señales,
entrada/salida, prompts, agentes, providers, riesgo, R:R, sesiones, cadencia,
frescura, unidades, costes, fills o capital durante los 14 días.

Todo fix permitido debe dejar un commit trazable y un dictamen de comparabilidad.
Si altera decisiones, sizing, ejecución, riesgo, PnL o la validez de las métricas,
se pausa el experimento y se determina con aprobación expresa un nuevo baseline
y reinicio del período; nunca se mezclan silenciosamente resultados de versiones.
Un problema de proveedor no autoriza a relajar gates ni cambiar providers.

## Validación y límites

- Suite completa: **507 tests OK**, incluidos 18 nuevos de seguridad y contratos.
- Slack: transporte falso; éxito, timeout, conexión, HTTP 429/5xx, deduplicación,
  persistencia anterior al envío y aislamiento de fallos verificados.
- `git diff --check`: PASS. Secret scan por patrones: sin hallazgos.
- Sin modificaciones en estrategia, archivos del Risk Engine, prompts/agentes,
  adapters de providers ni lógica del PaperBroker/TradeManager. Los fixes del
  runtime hacen cumplir los gates originales; el adaptador de cantidad corresponde
  a la aprobación expresa del usuario.
- No se enviaron mensajes reales de prueba, no se activó runner/scheduler,
  no se inició el experimento y no se desplegó Render en esta misión.

## Despliegue y activación pendientes — no incluidos en el freeze

Última observación read-only de Render: commit
`f3af0c38888dc32c309a5810a44773f1dce9f30f`, disco montado, quick_check=ok,
cero órdenes/posiciones/trades, experiment_started ausente, procesos runner y
scheduler ausentes. Configuración efectiva Twelve Data/FXMacroData/OpenAI y flags
de activación apagados. El Blueprint conserva macro=none; FXMacroData es un
override efectivo de Render que debe preservarse y verificarse al desplegar.

Antes de cualquier activación futura debe desplegarse este baseline o su commit
documental de freeze (código idéntico), comprobar SHA y configuración efectiva,
confirmar disco/SQLite y estado inicial, y obtener preflight de barras frescas
con el límite normal de 600s. Registrar fecha UTC e identidad del experimento solo
con autorización expresa de inicio. No usar diagnósticos o procesos paralelos
como una segunda autoridad sobre la base de producción.

Riesgos residuales aceptados/documentados:

- Datos de mercado stale por cierre: falta certificar frescura al reabrir.
- Render aún no contiene este baseline; **freeze no significa deployed/armed**.
- Los costes adicionales 0 son un modelo PAPER, no rentabilidad neta real.
- Gaps pueden superar el riesgo previsto del 1%; no hay garantía de liquidez ni
  fills reales. Gestión y supervisión ocurren por ciclos, no entre ellos.
- Slack puede quedar sin entregar tras timeout/crash: at-most-once prioriza no
  duplicar; SQLite y journal conservan la evidencia.
