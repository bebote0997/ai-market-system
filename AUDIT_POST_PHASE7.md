# Auditoría post Fase 7 — 2026-09-17

Estado actual: **INFRA_READY para despliegue controlado / EXPERIMENT_READY=false**. Bloqueo previo al experimento: **MACRO_PROVIDER_NOT_CERTIFIED**. La investigación y los adapters macro permanecen como evidencia histórica e inactivos.

## Cloud readiness con macro diferido — 2026-09-18 UTC

Se separó el preflight en infraestructura y experimento. El Blueprint conserva un solo servicio web y una sola instancia, disco persistente para SQLite/journal/outbox/lock, dashboard autenticado y de solo lectura, `AI_FLOOR_CLOUD_RUNNER=0`, `AI_FLOOR_SCHEDULER=0` y `AI_FLOOR_MACRO_PROVIDER=none`. El modo `none` usa `NoMacroDataProvider`: salud `NO_DATA`, Macro Agent `NO_DATA`, cero evidencia macro fabricada. Los adapters Finnhub y OfficialMacroProvider, junto con pruebas/investigación FRED, se preservan pero no están activos. EODHD Free devolvió HTTP **403** con respuesta HTML en una consulta mínima al [endpoint Economic Events](https://eodhd.com/financial-apis/economic-events-data-api) para 2026-09-18 a 2026-10-02; no hubo eventos evaluables ni se implementó otro adapter. No se harán más consultas macro en este hito.

El preflight `INFRA_READY` incluye montaje real, ruta SQLite durable, `quick_check` y escritura transaccional, contraseña privada y autoridad única; el de experimento exige adicionalmente macro certificado y credenciales operativas. `macro_provider_certified=false` hace `experiment_ready=false` aun si todos los demás requisitos pasan. El supervisor web y el runner comprueban el gate antes de arrancar, y el runner mantiene un lock exclusivo junto a la base. La exportación de revisiones conserva campos permitidos, redacta secretos y limita tamaño. Las alertas Slack se persisten antes de reclamar un `event_id`; los reinicios no reenvían intentos reclamados. Ninguna entrega real se envió.

Validación de infraestructura alcanzada **offline con mount/entorno simulados**; el preflight normal en esta máquina sigue `NOT_READY` porque no existe un mount Render ni credenciales de dashboard configuradas aquí. La validación de runtime en Render solo podrá hacerse después de un despliegue expresamente autorizado. No se considera el bloqueo macro un impedimento para preparar ese despliegue.

## Certificación controlada del híbrido oficial — 2026-09-18 UTC

`OfficialMacroProvider` conserva el contrato `MacroDataProvider` y consulta calendarios ICS oficiales de BLS, BEA y Eurostat, más feeds RSS oficiales del Federal Reserve y BCE. No necesita clave para esos endpoints; no se consultaron FRED ni Finnhub. Cada evento conserva URL e identificador de fuente, `DTSTAMP`/`pubDate` cuando existe, `fetched_at`, instante UTC o solo fecha según origen y campos de valores nulos cuando la fuente no los entrega. La relevancia HIGH es **clasificación determinista interna para XAUUSD/EURUSD**, no importancia informada por la fuente. Caché de seis horas y backoff negativo de una hora; sin consulta por cada ciclo de 15 minutos. `ReviewReport.macro_evidence` preserva la evidencia exacta disponible en la decisión.

Certificación live mínima (una petición inicial por fuente; BEA se verificó una vez más para corregir una interpretación local de `VALUE=DATE-TIME`, sin reintentar BLS):

| Fuente | Resultado real | Cobertura comprobada y límite |
| --- | --- | --- |
| [BLS ICS](https://www.bls.gov/help/hlpiCAL.htm) | **ACCESS_DENIED** en la petición oficial desde este entorno | Sin calendario live verificable de CPI y Employment Situation de EE. UU. |
| [BEA ICS](https://www.bea.gov/news/schedule/icalendar) | **READY**, 61 eventos tras corregir el parser | PIB y Personal Income and Outlays/PCE anunciados para 2026-09-30 12:30 UTC; `DTSTART` aporta hora y `DTSTAMP` actualización del calendario. No aporta actual, anterior ni consenso. |
| [Eurostat ICS](https://ec.europa.eu/eurostat/de/subscribe/ics.format) | **READY** | Calendario accesible; los eventos pertinentes observados usan `VALUE=DATE`: solo fecha, sin hora exacta. Ningún evento EUR de alta relevancia en la ventana de 14 días observado en esta pasada. |
| [Fed RSS](https://www.federalreserve.gov/feeds/feeds.htm) | **READY** | Comunicados FOMC ya publicados con `pubDate`; no calendario machine-readable de decisiones futuras en el feed. |
| [ECB RSS](https://www.ecb.europa.eu/home/html/rss.en.html) | **READY** | Decisiones monetarias ya publicadas con `pubDate`; no calendario machine-readable de decisiones futuras en el feed. |

Pasada inicial 2026-09-18 00:46:12 UTC: salud global **PARTIAL**, 11 publicaciones RSS ya emitidas (USD 10, EUR 1), 0 eventos futuros relevantes aceptados antes de corregir BEA. La comprobación puntual BEA 00:46:51 UTC confirmó el calendario y un evento PCE futuro; la corrección offline posterior reconoce también el título `GDP (Third Estimate)` de esa misma fecha. Estos resultados **no certifican** una agenda completa de USD ni EUR: falta el calendario BLS accesible para CPI/empleo y avisos futuros de decisiones FOMC y BCE mediante las fuentes machine-readable identificadas. No se fabricaron actual, anterior, consenso, importancia de origen ni hora para fechas Eurostat. RSS publicado nunca se presenta como aviso previo.

Cobertura por moneda: **USD PARTIAL** (BEA PIB/PCE futuro, Fed FOMC publicado; BLS CPI/empleo y FOMC futuro ausentes). **EUR PARTIAL** (Eurostat accesible pero horas imprecisas y sin evento pertinente en la ventana observada; BCE solo publicado, sin calendario futuro). No hay `source` actual/previous/consensus en los feeds elegidos. Ninguna de las fuentes oficiales probadas exige una API key, por lo que el bloqueo no es de credencial.

El modo `official_hybrid` queda configurable para análisis offline y futuras pruebas, pero `AI_FLOOR_MACRO_PROVIDER=none` se conserva en el Blueprint y preflight; runner/scheduler apagados. Finnhub **SUPPORTED / NOT_ACTIVE** y FRED **INSUFFICIENT / NOT_ACTIVE**. No se debilitó el requisito de awareness futuro para política monetaria, inflación y empleo.

## Evaluación FRED/ALFRED (posterior al rechazo Finnhub Free)

**FRED_INSUFFICIENT** como fuente macro única. Según los endpoints oficiales, `include_release_dates_with_no_data=true` da awareness futuro solo por fecha; `series/observations` y ALFRED aportan valores y vintages por fecha. El calendario API no entrega hora/zona exacta, consenso ni importancia, y no acredita cobertura completa de anuncios FOMC/ECB. Los timestamps de actualización por release/serie no prueban disponibilidad intradía de cada observación. No se implementó un `FredMacroDataProvider` activo porque sería inseguro convertir fechas a instantes del contrato actual. Ver `FRED_ALFRED_EVALUATION.md` para la matriz de siete criterios y enlaces oficiales.

Finnhub se conserva **SUPPORTED / NOT_ACTIVE / NOT_CERTIFIED**; no se reintentó. El Blueprint usa `AI_FLOOR_MACRO_PROVIDER=none`; el preflight cloud continúa fallando cerrado para activación. `MacroDataProvider` permanece intacto. No se solicitó `FRED_API_KEY`: una clave registrada es necesaria para la API FRED, pero no elimina las carencias documentadas.

Las pruebas previas de FRED/ALFRED mantienen el rechazo de revisiones/vintages sin publicación verificable antes de `as_of`. El híbrido añade pruebas deterministas de límite de publicación, cambios de zona horaria, fecha sin hora, caché, fallos parciales y procedencia en `ReviewReport`.

- Base aceptada: `18deed0a9c163c51c66ae8827d8edd5bddd983e4`; rama aislada `post-phase7-cloud`.
- Suite offline previa: 459 tests OK. Resultado final de la suite híbrida registrado abajo.
- Render: Blueprint local de una instancia web con disco persistente; runner/scheduler apagados, macro provider `none`; no desplegado. Prueba estructural estática de configuración OK; validación oficial CLI/API y preflight dentro de Render pendientes por ausencia de despliegue.
- Dashboard: contraseña de entorno antes de leer DB; lectura y export JSON acotada.
- SQLite: schema 2 → 3 con ledger de entrega y deduplicación macro. Se preservan runs, journal y PAPER.
- Slack: fake offline OK; ninguna entrega real. Entrega at-most-once por `event_id` con evento persistido antes del intento.
- `git diff --check`, secret scan y preflight final: resultados registrados abajo.
- Finnhub: adapter y no-lookahead offline OK. `FINNHUB_API_KEY` presente en `.env.local` del worktree (valor no leído en la auditoría). Certificación live mínima el 2026-09-17 23:23:43 UTC: GET Economic Calendar para 2026-09-16 a 2026-09-24; respuesta HTTP **403**, clasificada `AUTH_ERROR` por el adapter. Sin payload de calendario utilizable; entitlement y cobertura USD/EUR **NOT_CERTIFIED**. Un intento anterior dentro del sandbox falló por conexión; el resultado autoritativo es el 403 fuera del sandbox. No hubo reintentos posteriores.
- PAPER únicamente; XAUUSD/EURUSD habilitados, NAS100 deshabilitado, Twelve Data activo, Massive no activo, OpenAI `gpt-5.6-terra`; estrategia, riesgo y prompts intactos.
- Sin despliegue, scheduler, experimento, commit ni push.

## Verificación final local

- Suite offline completa: **470 tests OK**, código de salida 0; persisten advertencias de Streamlit en modo test y un `ResourceWarning` preexistente.
- `git diff --check`: **OK**; solo avisos de normalización CRLF de Git, sin errores de whitespace.
- Secret scan de 174 archivos versionados/no ignorados: **0 coincidencias** de credenciales con valor; `.env.local` no staged y staging vacío.
- Cloud preflight local real: **NOT_READY**, por ausencia de mount/durable path/DB de Render, contraseña y configuración cloud en este proceso; `macro_provider_disabled=true` y `macro_provider_certified=false`. Prueba estructural con mount y entorno simulados: **INFRA_READY**, `experiment_ready=false`; ausencia de contraseña o mount vuelve a `NOT_READY`. Render CLI no está instalado, por lo que no se ejecutó validación oficial del Blueprint; la prueba local valida sus campos de seguridad, servicio único, disco, variables y apagado.

Bloqueo reconocido **solo para el experimento**: `MACRO_PROVIDER_NOT_CERTIFIED`. La cobertura futura FOMC/BCE y CPI/empleo BLS sigue sin certificar; Finnhub y EODHD Free devolvieron HTTP 403. Investigación de proveedores diferida; no activar el runner.

## Auditoría final previa a publicación

- Worktree aislado `post-phase7-cloud` sobre la base aceptada `18deed0a9c163c51c66ae8827d8edd5bddd983e4`. Suite completa: **470 tests OK**, código de salida 0.
- `git diff --check`: **OK**; avisos CRLF de Git sin errores de whitespace.
- Secret scan de **174 archivos** versionados o candidatos a commit: cero patrones de API keys, tokens GitHub/Slack, webhooks o asignaciones de credenciales reales. Se contrastaron sin imprimir **6 valores de credenciales locales** contra todos esos archivos: cero coincidencias. Ningún archivo de claves privadas o credenciales entra en el conjunto. `.env.local` está ignorado, no versionado y no staged.
- PAPER-only y ejecución real deshabilitada; `AI_FLOOR_CLOUD_RUNNER=0` y `AI_FLOOR_SCHEDULER=0` en Blueprint, scheduler efectivo apagado, `experiment_started` ausente/no iniciado. `XAUUSD` y `EURUSD` habilitados; `NAS100` soportado pero deshabilitado. Macro efectivo `none`, Macro Agent `NO_DATA`; `MACRO_PROVIDER_NOT_CERTIFIED` mantiene `experiment_ready=false`.
- Infraestructura local con mount/entorno simulados: `INFRA_READY`; despliegue real, validación in-Render, scheduler y experimento siguen sin iniciarse. Render CLI no disponible localmente; validación estructural del Blueprint cubierta por pruebas offline.

## Prueba controlada de Slack en Render — 2026-09-18 UTC

- Servicio `ai-market-system-paper`, instancia única `r54kk`, desplegado desde `580558753c843a20d089b84dc883e19cdcdc4cb5`. Verificación en Web Shell: `AI_FLOOR_CLOUD_RUNNER=0`, `AI_FLOOR_SCHEDULER=0`, `AI_FLOOR_MACRO_PROVIDER=none`, `SLACK_WEBHOOK_URL` presente; `REAL_EXECUTION_ENABLED=False` comprobado antes del envío. No se imprimió el webhook.
- Se invocó una sola vez el `SlackNotificationSink` existente con evento de severidad `INFO`, tipo `AI TRADING FLOOR — SLACK TEST`, `event_id=slack-test-6c31427a097748f8a8e3045b7b9eab79`, sin símbolo, análisis, órdenes ni trades. El sink retornó tras respuesta **HTTP 200** de Slack; `SLACK_TEST=PASS` verificado a las 02:42 UTC. No se reintentó ni se inició runner, scheduler o experimento.
- La llamada fue una prueba manual aislada del sink; no constituye una ejecución de trading ni certifica entrega visual en el canal de Slack. `MACRO_PROVIDER_NOT_CERTIFIED` sigue bloqueando el experimento.
