# Auditoría de seguridad (2026-10-17) · rama `wip/oanda-adapter-clean`

Alcance: dashboard Streamlit en Render, conector OANDA (demo + vista Real de solo lectura), estado SQLite, logs, dependencias e historial git.
Nada de esto cambia Render ni despliega. «Corregido» = cambio de código con prueba en `test_security_audit.py`.

## Hallazgos

| # | Hallazgo | Severidad | Estado |
|---|---|---|---|
| 1 | Repositorio GitHub **público** por decisión del propietario (supervisión). Secret scanning y push protection: ON | Info | Aceptado: el repo no contiene secretos, tokens ni IDs de cuenta (escaneo del árbol y del historial publicado); todo valor sensible va solo por variables de entorno. Recomendado: activar Dependabot security updates |
| 2 | ID de cuenta de práctica OANDA como valor por defecto en el código | Media | **Corregido**: sin valor por defecto; `OANDA_ACCOUNT_ID` solo por entorno; sin cuenta → «sin cuenta» y el constructor falla cerrado. Pruebas: solo IDs sintéticos en todo el árbol |
| 3 | HSTS, CSP, X-Frame-Options, `nosniff` y Referrer-Policy no estaban en las respuestas (Streamlit no las pone y Render no permite cabeceras personalizadas) | Media | **Corregido**: `ui/security_headers.py` (middleware ASGI vía `st.App`, entrada `ui/asgi_app.py`) añade HSTS (1 año, includeSubDomains), CSP (frame-ancestors none, object-src none, base-uri/form-action self…), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, Permissions-Policy y COOP; verificado en un navegador headless (login, navegación y vista Control sin violaciones CSP). Palanca de vuelta atrás: `AI_FLOOR_SECURITY_HEADERS=0` en Render (arranca `ui/app.py` sin middleware) |
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
| 17 | Tokens OANDA no usados | Media | **Requiere acción del usuario**: revocar en OANDA Hub cualquier token que ya no se use (ninguno está en el código) |
| 18 | Validación de entradas: cantidades enteras, símbolos en lista cerrada (solo EURUSD), formatos de id de trade/cliente, `OANDA_ACCOUNT_ID` por patrón, límites de unidades y del script de prueba (≤100) | — | OK |

## Acciones que necesitan a Jeferson / Render (no hechas)

1. Activar Dependabot security updates en GitHub (opcional pero recomendado).
2. Revocar en OANDA Hub los tokens que ya no se usen; para la vista Real crear un token **de solo lectura/alcance mínimo si OANDA lo permite** y cargarlo como `OANDA_LIVE_TOKEN` y `OANDA_LIVE_ACCOUNT_ID` en Render (formulario seguro, nunca en chat ni en git). Solo se usa para GET summary.
3. Cuando decida activar la demo: `OANDA_PRACTICE_TOKEN`, `OANDA_ACCOUNT_ID` y `AI_FLOOR_BROKER_DEMO_ROUTING=1` (apagado por defecto).
4. Opcional: `STREAMLIT_SERVER_COOKIE_SECRET` estable; rotar la contraseña del dashboard periódicamente (≥12 caracteres, aleatoria).
