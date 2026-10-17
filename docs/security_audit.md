# Auditoría de seguridad (2026-10-17) · rama `wip/oanda-adapter`

Alcance: dashboard Streamlit en Render, conector OANDA (demo + vista Real de solo lectura), estado SQLite, logs, dependencias e historial git.
Nada de esto cambia Render ni despliega. «Corregido» = cambio de código con prueba en `test_security_audit.py`.

## Hallazgos

| # | Hallazgo | Severidad | Estado |
|---|---|---|---|
| 1 | El repositorio GitHub es **público** (estrategia, docs y código visibles). Secret scanning y push protection: ON; Dependabot security updates: OFF | Alta (exposición) | **Requiere acción**: volver el repo privado y activar Dependabot security updates |
| 2 | ID de cuenta de práctica OANDA estaba como valor por defecto en el código (repo público) | Media | **Corregido**: sin valor por defecto, `OANDA_ACCOUNT_ID` solo por entorno; sin cuenta → «sin cuenta», el constructor falla cerrado. ⚠️ El ID sigue en commits locales antiguos de la rama `wip/oanda-adapter`: usar la rama limpia `wip/oanda-adapter-clean` (historial sin el ID) para cualquier push |
| 3 | Sin HSTS, CSP, X-Frame-Options, `nosniff` ni Referrer-Policy en las respuestas reales (verificado con `curl -I`: solo cabeceras de Cloudflare/Render) | Media | **Requiere acción**: Streamlit no puede añadirlas y Render no permite cabeceras personalizadas en web services. Poner una Transform Rule/Response Header Modification en Cloudflare (o proxy propio) con: `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, y un CSP compatible con Streamlit |
| 4 | Cookie XSRF de Streamlit sin `Secure` detrás del proxy TLS de Render (Streamlit solo la marca con `sslCertFile`) | Baja | **Requiere acción/aceptar**: la sesión de login es estado del servidor (no cookie de autenticación), impacto bajo. Opcional: fijar `STREAMLIT_SERVER_COOKIE_SECRET` estable en Render |
| 5 | HTTPS: el puerto 80 no responde (no hay downgrade); HTTPS con HTTP/2 vía Cloudflare | Info | OK; verificar «Redirect HTTP→HTTPS» en Cloudflare |
| 6 | Autenticación del dashboard: `hmac.compare_digest`, contraseña ≥12, retraso por fallo, bloqueo por sesión (5 intentos/300 s) y global (20/900 s), sesión caduca a 8 h, nada se renderiza antes de `require_dashboard_access` | — | OK; añadidas pruebas estáticas (orden de la compuerta, nada al importar, router y balances inalcanzables sin login) |
| 7 | Endpoints sin login: `/_stcore/health` (necesario para Render, solo «ok») y `/_stcore/host-config` (configuración pública de orígenes), `/static` 301. No exponen datos de trading ni secretos | Baja | Aceptado; no se sirven ficheros de datos (`enableStaticServing=false`) |
| 8 | Flags de Streamlit dependían de defaults | Baja | **Corregido**: `--server.enableXsrfProtection=true`, `--server.enableCORS=true`, `--browser.gatherUsageStats=false`, `--server.enableStaticServing=false`, `--client.toolbarMode=viewer` (+ ya `showErrorDetails=none`); prueba que impide debilitarlos |
| 9 | Posibles secretos/IDs de cuenta en logs y mensajes de error | Media | **Corregido**: `execution/log_redaction.py` (record factory + `formatException`) instalado en `runtime.cloud`, `runtime.cloud_runner`, `runtime.__main__` y `ui/app.py`; redacta valores de variables con TOKEN/SECRET/PASSWORD/API_KEY/WEBHOOK/COOKIE, `Bearer …`, forma de token OANDA, `sk-…`, webhooks de Slack, userinfo en URLs e IDs de cuenta; `review_export` usa el mismo redactor |
| 10 | Ficheros SQLite (trading DB, `broker_state.db`) legibles por otros usuarios del host | Media | **Corregido**: `umask 0o077` antes de lanzar procesos hijos en `runtime.cloud` y, al arrancar, endurecimiento a `0o600` de la trading DB existente y sus `-wal/-shm` (por descriptor, sin seguir symlinks); `broker_state.db` y `-wal/-shm` a `0o600` (solo en directorios creados por nosotros, nunca chmod de directorios existentes). No se sirven por web |
| 11 | Cifrado en reposo del estado | Baja | **Parcial / acción**: el disco de Render se cifra a nivel de plataforma (verificar en Render). Cifrado a nivel de app **no implementado**: el estado solo guarda ids de trade, precios y eventos, **no** credenciales ni ID de cuenta completo |
| 12 | TLS en llamadas salientes | — | OK: sin `verify=False`/`CERT_NONE`/`shell=True`/`http://` externo en el código (prueba de escaneo con casos plantados); el cliente OANDA solo acepta hosts https de una lista cerrada y no sigue redirecciones |
| 13 | Secretos solo por entorno; `.env.example` sin valores secretos; `render.yaml` con `sync: false` para cada secreto y sin claves OANDA/DXTRADE | — | OK, con pruebas |
| 14 | Escaneo de historial git (278 commits, todas las ramas) y árbol: sin claves reales; solo valores falsos en tests. Único dato sensible: el ID de práctica (ver #2, no pusheado) | — | OK |
| 15 | Dependencias: `pip-audit -r requirements-ci.txt` → sin vulnerabilidades conocidas (2026-10-17); versiones fijadas con hashes | — | OK; activar Dependabot (ver #1) |
| 16 | Integridad del kill switch y guarda de host live: orden solo a `api-fxpractice.oanda.com` (sin flag para live), cliente de solo lectura con GET `/accounts/{id}/summary` únicamente, aislado del camino de órdenes (escaneo de imports), token live solo de `OANDA_LIVE_TOKEN`, `REAL_EXECUTION_ENABLED=False`, cuenta real nunca habilitable | — | OK, con pruebas |
| 17 | Token OANDA **real** generado antes por Jeferson | Alta si sigue activo | **Requiere acción del usuario**: revocarlo en OANDA Hub (no se usa en ningún sitio) |
| 18 | Validación de entradas: cantidades enteras, símbolos en lista cerrada (solo EURUSD), formatos de id de trade/cliente, `OANDA_ACCOUNT_ID` por patrón, límites de unidades y del script de prueba (≤100) | — | OK |

## Acciones que necesitan a Jeferson / Render (no hechas)

1. Volver privado el repositorio; activar Dependabot security updates.
2. Cabeceras de seguridad y HSTS vía Cloudflare (hallazgo #3); confirmar «Always Use HTTPS».
3. Revocar el token OANDA de la cuenta real generado antes; si quiere la vista Real, crear un token **de solo lectura/alcance mínimo si OANDA lo permite** y ponerlo como `OANDA_LIVE_TOKEN` y `OANDA_LIVE_ACCOUNT_ID` en Render (formulario seguro, nunca en chat ni en git). Solo se usa para GET summary.
4. Poner en Render (solo cuando él decida): `OANDA_PRACTICE_TOKEN`, `OANDA_ACCOUNT_ID`, y para activar el broker demo `AI_FLOOR_BROKER_DEMO_ROUTING=1` (apagado por defecto).
5. Usar la rama limpia (sin ID de cuenta en el historial) para cualquier push/PR.
6. Rotar la contraseña del dashboard periódicamente (≥12 caracteres, aleatoria).
