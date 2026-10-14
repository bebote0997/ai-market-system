# Fase 6D — integración de proveedores reales

Estado: integración local implementada y certificación live activa **PASS** con OpenAI y Twelve Data el 17 de septiembre de 2026. Massive permanece soportado/configurable pero NOT_ACTIVE; su historial de fallos no bloquea la demo. NAS100 permanece soportado/NOT_ENABLED. El experimento PAPER de 14 días aún no se inicia.

## Alcance del experimento PAPER

El catálogo de símbolos soportados permanece XAUUSD, NAS100 y EURUSD. Para el experimento de 14 días, enabled_symbols es XAUUSD y EURUSD. NAS100 sigue implementado mediante I:NDX, pero queda NOT_ENABLED y NOT_CERTIFIED; ni el scheduler, ni un ciclo manual, ni el certificador lo consultan mientras está desactivado. No se elimina código, no se sustituye el instrumento y no se cambia ningún plan. La certificación del experimento puede llegar a PASS con los dos símbolos habilitados aunque I:NDX reciente carezca de entitlement. Rehabilitar NAS100 exige una nueva configuración y su propia certificación.

## Límites

- MassiveMarketDataProvider implementa MarketDataProvider sin modificar Risk Engine ni Paper Broker. Mapeo: XAUUSD → C:XAUUSD, EURUSD → C:EURUSD, NAS100 → I:NDX. El símbolo de datos no determina especificaciones contractuales de ejecución.
- Los tres intervalos usan agregados oficiales de Massive. El timestamp t es el inicio de la ventana en milisegundos Unix. Se descartan barras cuya ventana no ha terminado en as_of; no hay forward fill ni remuestreo.
- La respuesta se valida antes de crear frames: ticker exacto, orden temporal, unicidad, OHLC finito y coherente. Falta de barras produce NO_DATA; barras retrasadas quedan sujetas al freshness gate.
- OpenAIProvider usa Responses API con JSON Schema estricto y store=false. ai.runtime.call_agent sigue validando identidad y grounding contra AIRequest. El modelo configurable tiene valor inicial gpt-5.6-terra, pendiente de certificar en la cuenta.
- Timeout y reintentos son acotados. Credenciales via variables de entorno o .env.local ignorado. La suite de pruebas no usa red ni claves.
- OperationalRuntime conserva inyección de dependencias. Modos: none/massive y deterministic/openai; ningún modo habilita ejecución real.

## Certificación

Ejecutar desde la raíz: python -m runtime.certify_providers --json. Hace una respuesta estructurada mínima de OpenAI y comprueba referencia, 1h, 15m, 5m, velas cerradas y frescura de cada símbolo habilitado de Massive. Los símbolos soportados pero desactivados se registran como NOT_ENABLED y no se consultan. No llama a estrategia, Risk Engine ni Paper Broker. Imprime solo metadatos seguros y estado PASS, PARTIAL o FAIL.

La certificación PASS de este experimento requiere credenciales locales, acceso al modelo y cobertura Massive vigente para XAUUSD y EURUSD en los tres intervalos. No depende de I:NDX mientras NAS100 esté desactivado. PARTIAL no autoriza la demo oficial de 14 días. Nunca se compra ni cambia un plan automáticamente.

## Evidencia local

- Base 71c82a2: 405 tests OK tras instalar Plotly, dependencia de UI ya declarada.
- Suite offline de Fase 6D: 421 tests OK. Incluye que un PASS de XAUUSD/EURUSD no consulta NAS100, que NAS100 queda NOT_ENABLED, que el runtime rechaza ciclos manuales para símbolos desactivados y que una cuota OpenAI agotada se clasifica sin reintentos ni exposición del cuerpo de error.
- Certificación local sin credenciales en el worktree: PARTIAL. XAUUSD y EURUSD figuran NOT_CONFIGURED para Massive; NAS100 figura NOT_ENABLED/NOT_CERTIFIED. OpenAI figura NOT_CONFIGURED en el worktree porque su clave está en el .env.local del checkout principal, no porque el modelo haya fallado. No se hizo llamada live a OpenAI.
- Certificación en vivo del runtime: pendiente por MASSIVE_API_KEY local. La clave OpenAI existente de Default project ha sido aceptada por el usuario; no se creará otra.
- git diff --check: sin errores. Secret scan de archivos de código y documentación: sin coincidencias; .env.local ignorado por Git.
- El conector Massive reconoció C:XAUUSD, C:EURUSD e I:NDX en referencia. Para I:NDX, una consulta de 5m del 16 de septiembre devolvió NOT_ENTITLED; una consulta histórica del 14–15 de septiembre sí devolvió barras. Esto evidencia una restricción temporal de acceso, no ausencia del instrumento. Las consultas recientes de FX devolvieron EMPTY; una consulta diaria histórica de XAUUSD devolvió barras. Las consultas posteriores alcanzaron RATE_LIMIT. No se declara cobertura operacional ni frescura con estos resultados.

## Certificación live solicitada el 17 de septiembre de 2026

- Archivo local indicado: C:/Users/jefer/Documents/ai-market-system/.env.local. La comprobación de nombres, sin leer valores en la salida, encontró OPENAI_API_KEY y no encontró MASSIVE_API_KEY. El entorno del proceso y el worktree tampoco tienen MASSIVE_API_KEY. Por ello no se realizaron llamadas live a Massive desde el runtime; XAUUSD y EURUSD siguen NOT_CONFIGURED y NAS100 permanece NOT_ENABLED.
- OpenAI: solicitud mínima estructurada a gpt-5.6-terra; respuesta HTTP 429 clasificada RATE_LIMITED. Un intento inicial dentro del sandbox devolvió CONNECTION_ERROR; el intento fuera del sandbox alcanzó la API y devolvió RATE_LIMITED. La clasificación posterior distinguió códigos explícitos de cuota agotada, pero la nueva respuesta volvió a ser RATE_LIMITED. No se confirmó disponibilidad del modelo ni validez del contrato en vivo. Se suspendieron los reintentos inmediatos.
- Resultado general: PARTIAL. No se inicia el experimento, no se hace commit ni push.

## Reintento Massive del 17 de septiembre de 2026

- MASSIVE_API_KEY presente en el archivo local indicado; la comprobación mostró solo presencia, nunca el valor.
- La primera ejecución dentro del sandbox de red devolvió PROVIDER_ERROR para ambos símbolos. Se repitió fuera del sandbox conforme a las reglas del entorno.
- XAUUSD: referencia C:XAUUSD PASS; intervalos 1h, 15m y 5m PASS; freshness STALE_DATA. El dato no se declara CURRENT ni se habilita progresión PAPER.
- EURUSD: referencia C:EURUSD PASS; consulta de barras bloqueada por RATE_LIMITED. Los intervalos y la frescura siguen sin certificar.
- NAS100: soportado como I:NDX, NOT_ENABLED y NOT_CERTIFIED; no se consultó.
- Massive global: PARTIAL. No se hicieron más llamadas tras RATE_LIMITED. OpenAI no se reintentó en esta pasada porque Massive no alcanzó PASS. No hay commit ni push.

## Certificación controlada del 17 de septiembre de 2026, 02:06 UTC

- Precheck: `OPENAI_API_KEY` y `MASSIVE_API_KEY` presentes en el `.env.local` del checkout principal; solo se comprobó presencia. El archivo está ignorado por Git. `enabled_symbols=(XAUUSD, EURUSD)`. NAS100 conserva el mapping `I:NDX`, figura soportado/NOT_ENABLED/no certificado y no se consultó.
- El certificador evitó consultas redundantes de referencia: validó el ticker exacto en cada respuesta OHLC. Se consultaron únicamente los intervalos requeridos. Los umbrales de frescura permanecen 600 s (5m), 1800 s (15m) y 7200 s (1h). Los timestamps `t` se interpretaron como inicio UTC de cada barra cerrada; no hubo relleno ni uso de barra en formación.
- Massive XAUUSD (`C:XAUUSD`): ticker y OHLC 5m/15m/1h recibidos. A las `2026-09-17T02:06:23.170642+00:00`, últimas barras UTC: 5m `2026-09-16T23:56:00+00:00` (edad 7823.171 s), 15m `2026-09-16T23:51:00+00:00` (8123.171 s), 1h `2026-09-16T23:00:00+00:00` (11183.171 s). Todas superan los umbrales. Massive informó FX OPEN, pero eso no demuestra el estado del mercado del metal: `MARKET_STATUS_UNKNOWN`, fail closed. No certificado.
- Massive EURUSD (`C:EURUSD`): ticker y OHLC 5m/15m recibidos. A la misma hora, últimas barras UTC: 5m `2026-09-16T23:56:00+00:00` (7823.171 s), 15m `2026-09-16T23:51:00+00:00` (8123.171 s), ambas `STALE_DATA` con FX OPEN. La consulta 1h terminó en HTTP 429 `RATE_LIMITED`, sin `Retry-After`; un reintento acotado tras backoff tampoco permitió completarla. No certificado. Massive global: PARTIAL.
- OpenAI: una sola llamada de certificación a Responses con `gpt-5.6-terra`, sin reintentos. HTTP 429, `type=insufficient_quota`, `code=credit_balance_exhausted`, sin `Retry-After`: `QUOTA_EXHAUSTED`. Es un bloqueo externo de cuota/saldo de API; el contrato estructurado live y la disponibilidad del modelo no pudieron validarse. El código se corrigió para clasificar este tipo/código sin reintento y se agregó prueba offline determinista.
- Verificación local tras el diagnóstico: suite offline completa 425 tests OK. `git diff --check` pasó; el escaneo de patrones de claves en el worktree no encontró coincidencias; `.env.local` permanece ignorado. Ningún secreto se mostró ni se preparó para commit.
- No se inicia el experimento PAPER de 14 días, Fase 7, ni ejecución real. No se hace commit ni push mientras la certificación no sea PASS.

## Recertificación controlada del 17 de septiembre de 2026, 17:30 UTC

- `OPENAI_API_KEY` y `MASSIVE_API_KEY` presentes en el `.env.local` del checkout principal; solo se verificó presencia. Git ignora ese archivo. Se conserva `enabled_symbols=(XAUUSD, EURUSD)` y NAS100 soportado como `I:NDX`, desactivado y sin consultas.
- OpenAI: se hizo una sola llamada a Responses con `gpt-5.6-terra`, sin reintentos. La API devolvió salida estructurada convertida a `AIResponse`, `contract=ok` para identidad y grounding y metadatos de uso (`input_tokens=554`, `output_tokens=221`, `total_tokens=775`). No hubo HTTP 429. El certificador produjo `FAIL` porque aceptaba únicamente `OK`/`PARTIAL` para un prompt que permite `NO_DATA`; además no registraba el `response_status` devuelto. Esa respuesta no se guardó y no puede determinarse retrospectivamente si fue `NO_DATA` o `ERROR`, por lo que esta ejecución **no se declara PASS**. Se corrigió la condición para aceptar `NO_DATA` válido, exigir usage y registrar `response_status`; una prueba offline comprueba `NO_DATA` válido y otra comprueba que `ERROR` siga fallando. No se hizo una segunda llamada live por el límite de una llamada solicitado.
- Massive: certificación a `2026-09-17T17:30:54.021562+00:00`, con seis consultas OHLC y una consulta de estado FX, sin HTTP 429. Ticker exacto verificado en las respuestas. FX reportó `OPEN`.
- XAUUSD `C:XAUUSD`: última barra 5m `2026-09-16T23:55:00+00:00` (edad 63354.022 s, umbral 600 s); 15m `2026-09-16T23:45:00+00:00` (63954.022 s, umbral 1800 s); 1h `2026-09-16T23:00:00+00:00` (66654.022 s, umbral 7200 s). El estado FX no certifica el horario del metal: `MARKET_STATUS_UNKNOWN`, fail closed.
- EURUSD `C:EURUSD`: mismas últimas barras, edades y umbrales; con FX `OPEN`, las tres quedan `STALE_DATA`. La cobertura OHLC y ticker pasó, pero la frescura no. Massive global: PARTIAL. NAS100 siguió `NOT_ENABLED` y `NOT_CERTIFIED`.
- Cierre bloqueado por datos Massive antiguos; se necesita que el proveedor entregue barras actuales de XAUUSD y EURUSD, o evidencia verificable del estado de sesión correspondiente, antes de repetir la certificación. No se cambian umbrales, plan, modelo ni instrumentos. No hay commit/push ni se inicia el experimento de 14 días.
- Verificación tras los cambios: 427 tests offline OK; `git diff --check` OK; escaneo de patrones de secretos sin coincidencias; ningún archivo staged. `.env.local` sigue ignorado.

## Cambio de proveedor activo y certificación PASS, 17 de septiembre de 2026

- Decisión aprobada: Twelve Data es el `MarketDataProvider` activo de la demo PAPER. Mapping oficial XAUUSD → `XAU/USD`, EURUSD → `EUR/USD`. Massive conserva su adapter y mapping NAS100 → `I:NDX`, pero figura NOT_ACTIVE; NAS100 figura SUPPORTED/NOT_ENABLED y no se consulta.
- Precheck: `OPENAI_API_KEY` y `TWELVE_DATA_API_KEY` presentes en el `.env.local` ignorado del checkout principal; se verificó solo presencia. El cargador local fue corregido para incluir los nombres de Twelve Data. Ningún valor se imprimió, registró ni preparó para Git.
- Primera ejecución del certificador con la nueva arquitectura: OpenAI llegó a Responses y produjo `NO_DATA` con usage, pero el contrato informó `agent_mismatch`; Twelve Data se marcó NOT_CONFIGURED porque el cargador local aún no admitía su variable. Fueron errores locales corregidos con pruebas offline. Se hizo una pasada efectiva controlada después de las correcciones; no se consultó Massive.
- Corrección OpenAI: el JSON Schema estricto fija `schema_version`, `run_id`, `as_of`, `symbol` y `agent_name` a los valores de la solicitud mediante enums de un valor. La validación de identidad y grounding sigue activa; no se reescribe la respuesta. `NO_DATA` contractual es aceptado con usage positivo; `ERROR` continúa fallando.
- Pasada live efectiva a `2026-09-17T17:54:42.306837+00:00`: OpenAI `gpt-5.6-terra` PASS, `response_status=NO_DATA`, `contract=ok`, usage 599/241/840 tokens (entrada/salida/total). Twelve Data PASS para XAUUSD `XAU/USD` y EURUSD `EUR/USD` en 5m/15m/1h. Metadatos de ticker e intervalo exactos; health METAL/FOREX READY. No hubo 429, entitlement ni datos vacíos.
- Para ambos símbolos, últimas barras UTC: 5m `17:45` (edad 582.307 s, umbral 600 s), 15m `17:30` (1482.307 s, umbral 1800 s), 1h `16:00` (6882.307 s, umbral 7200 s). Las ventanas cerraron a 17:50, 17:45 y 17:00 UTC respectivamente. El estado de sesión permanece UNKNOWN porque no se gastó un request adicional para inferirlo; la frescura se certificó con barras cerradas dentro de los límites originales. Ver matriz completa en `PROVIDER_CERTIFICATION.md`.
- Massive NOT_ACTIVE y NAS100 NOT_ENABLED no intervinieron en el cálculo de PASS. Scheduler deshabilitado, PAPER únicamente; no se inicia Fase 7 ni los 14 días.
- Verificación final: suite offline completa 435 tests OK; `git diff --check` OK; escaneo de patrones de credenciales en el worktree sin coincidencias. `.env.local` continúa ignorado y nunca entra al commit.

## Referencias de contratos

- [Massive REST quickstart](https://massive.com/docs/rest/quickstart): autenticación Bearer.
- [Massive Custom Bars indices](https://massive.com/docs/indices/get_v2_aggs_ticker__indicesticker__range__multiplier___timespan___from___to): t es inicio de ventana en milisegundos.
- [Massive Forex overview](https://massive.com/docs/rest/forex/overview): timestamps normalizados a UTC.
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/responses-vs-chat-completions): text.format con JSON Schema estricto.
