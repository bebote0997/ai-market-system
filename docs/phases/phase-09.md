# Fase 9 — Email Notification Engine

**Estado:** CERTIFIED (ligera, PLAN_RAPIDO; 2026-10-11, control delegado a Grok Bot, pendiente de confirmación del owner).

**Tareas (Libro v1.9):** VALID/EXECUTED TRADE; ORDER SUBMITTED; POSITION OPENED; POSITION CLOSED; RISK REJECTED; AI CAUTION; PROVIDER FAILURE; SYSTEM DEGRADED; SYSTEM CRITICAL; RECOVERY; contenido completo de alerta de trade (incl. Chart, Risk %, Session, Quantity — ISSUE-007); chart e identidad visual; diseño luxury/institutional/mobile; deduplication; delivery status; email failure detection; retry policy; test emails; alert priority (F09-T01…T19)

**Gate:** requiere fase anterior CERTIFIED y autorización explícita del owner.

**Implementación:**
- `runtime/alert_catalog.py` — catálogo de los 10 eventos con prioridad, contenido de alerta de trade (valores del llamador tal cual; ausentes = `NO_DATA`), mapeo desde el contrato durable `NotificationEvent`.
- `runtime/alert_render.py` — una plantilla HTML responsive (oscura/dorada, sin imágenes remotas, valores escapados) + texto plano.
- `runtime/alert_channels.py` — Email Gmail SMTP (`smtplib.SMTP_SSL` `smtp.gmail.com:465`, `GMAIL_APP_PASSWORD`; remitente/destinatario `AI_FLOOR_ALERT_EMAIL_FROM`/`_TO`, por defecto yefersontejeda@gmail.com) y Slack (`SLACK_WEBHOOK_URL`, reutiliza `runtime/notifications.py`). Respuestas SMTP clasificadas: 4xx → `TEMPORARY_FAILURE` (reintentable), 5xx → `REJECTED` / `AUTH_ERROR` (permanente); el cierre (QUIT/close) va aparte: si `send_message` ya tuvo éxito, un fallo de cierre solo se registra y la entrega queda `DELIVERED` sin reintento (Codex M1/M2).
- `runtime/alert_dispatch.py` — dedup por `event_id` y estado de entrega durable por canal (ledger SQLite), retry con backoff exponencial acotado, detección de fallos por canal; **OFF por defecto** (`AI_FLOOR_ALERTS_ENABLED` debe ser exactamente `1`); nunca lanza excepciones. Claims `SENDING` huérfanos (proceso caído): tras `stale_after` (300 s por defecto, mínimo 60 s) se re-reclaman de forma atómica (CAS sobre `claimed_at`), se reenvían y la fila registra `recovery=AMBIGUOUS_RETRY` (posible duplicado, nunca supresión permanente); un remitente lento ya no puede sobrescribir el resultado del claim vigente (Codex M3).
- `runtime/alert_chart.py` — F09-T12: sparkline SVG inline a partir de los cierres de barras que entrega el llamador (sin dependencias nuevas); PNG adjunto vía plotly+kaleido **solo** si kaleido ya está instalado (hoy no lo está); sin barras → `NO_DATA`.
- `runtime/alert_hook.py` — #25: cola acotada (1000 eventos por defecto), admisión `put_nowait`, descarte + log `QUEUE_FULL` al saturarse; un único worker daemon hace mapeo, persistencia, envío y retries. `flush(timeout)` espera recuperación inicial y trabajo admitido; `close(timeout)` corta admisión y drena con join acotado (runner usa timeout 0). Cableado: `DemoRunner._capture_and_deliver` llama al hook **después** del ciclo, con los `NotificationEvent` ya persistidos por `capture_notifications`, dentro de un try/except que solo registra. Gated por `AI_FLOOR_ALERTS_ENABLED` (por defecto 0: no se construye dispatcher ni canal, no se mapea nada). Ledger opcional durable con `AI_FLOOR_ALERTS_LEDGER_PATH` (por defecto en memoria). #24: al inicializar el hook habilitado, el worker recupera snapshots pendientes del ledger sin esperar nuevos eventos; revisita claims frescos cada segundo y los recupera cuando vencen. `alert_events` almacena el snapshot completo antes del envío; estados `DELIVERED`/`FAILED` son terminales por canal. CAS y `AMBIGUOUS_RETRY` siguen vigentes.
- `runtime/alert_test.py` — `python -m runtime.alert_test --slack` / `--email`: envía UNA alerta TEST por el mismo camino (acción manual del owner; no se ejecuta en tests; imprime solo estados tipados).

**Invariantes:** no está conectado a ningún camino de órdenes/trading: el único punto de cableado es `runtime/alert_hook.py`, invocado por el runner tras el ciclo, nunca por `runtime/service.py` (test estructural); un canal que falla no cambia órdenes/posiciones (test con estado PAPER real); tests solo con mocks (`smtplib.SMTP_SSL` y `urlopen` parcheados; cualquier `socket.create_connection` hace fallar el test). Sin cambios en estrategia/riesgo/broker, flags por defecto ni Render.

## F09 → tests (`test_phase9_alerts.py`)
| ID | Requisito | Tests | Cobertura |
|---|---|---|---|
| F09-T01…T10 | Los 10 eventos (VALID/EXECUTED TRADE … RECOVERY) | `TestCatalog::test_f09_t01_t10_ten_events_with_priority`<br>`TestCatalog::test_maps_existing_durable_notification_contract` | COVERED |
| F09-T11 | Contenido completo de alerta de trade (símbolo, lado, entry, SL, TP, cantidad, R:R, Risk %, sesión, run_id, setup_id, tiempos) | `TestCatalog::test_f09_t11_trade_content_complete`<br>`TestCatalog::test_f09_t11_absent_values_are_no_data_never_invented`<br>`TestCatalog::test_f09_t11_values_reported_verbatim_and_snapshotted` | COVERED |
| F09-T12 | Chart e identidad visual | `TestRender::test_f09_t12_t13_responsive_html_escaped`<br>`TestChart::test_f09_t12_sparkline_from_closes`<br>`TestChart::test_f09_t12_rendered_inline_or_no_data`<br>`TestChart::test_f09_t12_png_only_when_kaleido_installed` | COVERED (PNG solo si kaleido instalado) |
| F09-T13 | Diseño luxury/institutional/mobile | `TestRender::test_f09_t12_t13_responsive_html_escaped` | COVERED |
| F09-T14 | Deduplication | `TestDispatcher::test_f09_t14_t15_dedup_and_durable_status`<br>`TestCatalog::test_event_id_deterministic` | COVERED |
| F09-T15 | Delivery status | `TestDispatcher::test_f09_t14_t15_dedup_and_durable_status`<br>`TestDispatcher::test_not_configured_channel_is_not_claimed` | COVERED |
| F09-T16 | Email failure detection | `TestDispatcher::test_f09_t16_failure_detection`<br>`TestEmailChannel::test_f09_t16_errors_typed_and_secret_free`<br>`TestDispatcher::test_f09_t17_retry_exhaustion_and_non_retryable`<br>`TestSmtpCleanupAndClassification::test_m2_reply_codes_classified` | COVERED |
| F09-T17 | Retry policy | `TestDispatcher::test_f09_t17_bounded_backoff_retry`<br>`TestDispatcher::test_f09_t17_retry_exhaustion_and_non_retryable`<br>`TestSmtpCleanupAndClassification::test_m1_quit_failure_after_send_is_delivered_once_without_retry`<br>`TestSmtpCleanupAndClassification::test_m1_send_error_wins_over_cleanup_error`<br>`TestSmtpCleanupAndClassification::test_m2_dispatcher_retries_4xx_and_stops_on_5xx`<br>`TestStaleClaimRecovery::*` | COVERED |
| F09-T18 | Test emails | `TestRender::test_f09_t18_test_alert_is_labelled`<br>`TestEmailChannel::test_sends_via_gmail_smtp_ssl_with_html_and_text` (mock) | PARTIAL |
| F09-T19 | Alert priority | `TestDispatcher::test_f09_t19_priority_order`<br>`TestCatalog::test_f09_t01_t10_ten_events_with_priority` | COVERED |
| Invariante | Fallo de alerta no crea/altera órdenes; OFF por defecto; cableado solo post-ciclo | `TestTradingIsolation::*`<br>`TestDispatcher::test_off_by_default`<br>`TestDispatcher::test_env_example_and_render_keep_alerts_off`<br>`TestDispatcher::test_dispatch_never_raises`<br>`TestAlertHook::*`<br>`TestRunnerWiring::test_disabled_hook_makes_no_calls_and_failures_never_change_paper_state` | COVERED |

Revisión Codex (PR #21): M1, M2, M3 corregidos (ver arriba y `TestSmtpCleanupAndClassification`, `TestStaleClaimRecovery`).

Nota T12: los cierres de barras deben venir del llamador; el mapeo desde `NotificationEvent` todavía no los aporta (el contrato durable no los contiene), por lo que las alertas cableadas muestran Chart `NO_DATA` hasta que el owner decida la fuente. Muchos clientes de correo (p. ej. Gmail web) no muestran SVG inline; el PNG adjunto requiere kaleido (dependencia no autorizada; no instalada).

PARTIAL: T18 (`make_test_alert` produce un TEST etiquetado y se valida con mocks; un envío real de prueba requiere `GMAIL_APP_PASSWORD` y autorización explícita del owner).

**Pendiente para certificar:** re-revisión independiente (M1–M3 + cableado), envío real de prueba T18 (`python -m runtime.alert_test --slack`) con autorización del owner, aprobación del owner. Activación (`AI_FLOOR_ALERTS_ENABLED=1`) sigue fuera de alcance.

## Correcciones #24 / #25 — 2026-10-11

- SMTP y Slack: timeout de transporte por defecto 10 s; valores fuera de `(0, 10]` rechazados. Reintentos y backoff solo en el worker del hook, nunca en el runner.
- Tests offline añadidos: canal mock que duerme 5 s → llamada al hook <50 ms; cola llena; cierre/flush acotados; retries en un único worker daemon; límites de timeout; reinicio con ledger SQLite durable (stale + pendiente sin claim, no repetir terminales, preservar snapshot); claim fresco que caduca después del arranque; ledger legado sin snapshot → `RECOVERY_NO_DATA`.
- La admisión en la cola no es durable: caída antes de persistir en el worker o overflow puede perder una alerta. Ledgers anteriores sin contenido no permiten reconstruir eventos; se registra `RECOVERY_NO_DATA`, sin inventar datos. Si el llamador vuelve a entregar el evento original, se guarda su snapshot y se aplica la recuperación existente.
- El hook devuelve conteos `queued`/`dropped`, no estados de entrega síncronos; los estados finales se consultan en el ledger antes de cerrar el worker. El orden del hook es FIFO; la prioridad del dispatcher sigue disponible en `dispatch_many`.
- PAPER/DEMO, flags OFF, sin red real, dependencias nuevas, activación ni deploy. Pendiente revisión independiente y aprobación del owner; F09-T18 sigue PARTIAL.
- Validación: suite completa solicitada **1475 tests OK (1 expected failure)**; suite de alertas **49 tests OK**; `ruff check .` y `git diff --check` PASS. Sin commit.


## Small fixes autorizados — 2026-10-11

- T12 usa el almacén existente `market_evidence` (sidecar de SQLite, no tabla en `trading_floor.db`), mediante `AI_FLOOR_MARKET_EVIDENCE_PATH` y URI `mode=ro`. Últimas 48 velas cerradas de 5m del símbolo, cronológicas, con cierre ≤ timestamp del evento; sin proveedor ni datos inventados. Ausencia/error de lectura → `NO_DATA` con error tipado en log.
- Preparación de eventos nuevos y recuperados en el worker `alert-delivery`. PNG mediante matplotlib Agg, imagen relacionada inline con CID en HTML; import/render fallido → SVG. Sin velas → `NO_DATA`. Se sustituye plotly/kaleido para esta ruta.
- Recuperación periódica de SENDING: constante de 60 s, parámetro `recovery_interval` para tests; recuperación inicial inmediata conservada.
- Dependencia autorizada `matplotlib==3.10.7` en `requirements.txt`, incluido por `requirements-ci.txt`. Intento de pip en `/workspace/repo/.venv` bloqueado por resolución DNS del índice: instalación real NO VERIFICADA; rutas de Agg/import/render verificadas con mocks.
- Tests offline con mocks: consulta read-only/cutoff/símbolo/límite/orden, worker y replay, CID/fallback, Agg/import/render fallido, intervalo y validación. Lector solo stdlib; sin importar engine ni storage. Allowlist del test de consumidores de evidencia incluye este lector autorizado; aislamiento de imports de alertas conservado.
- Sin certificación, activación, envío real, deploy ni commit.

Validación final: suite completa solicitada, 1495 tests OK (1 expected failure), 176.371 s; Ruff y git diff --check PASS.


## Certificación ligera (2026-10-11)
- [x] PR #21: motor de alertas (https://github.com/bebote0997/ai-market-system/pull/21), merge 4452350, CI https://github.com/bebote0997/ai-market-system/actions/runs/38112696799. Revisión de Codex: 3 MEDIUM corregidos. Re-review: 0 CRITICAL/HIGH.
- [x] PR #27: worker aislado y recuperación al arranque, cierra #24 y #25 (https://github.com/bebote0997/ai-market-system/pull/27), merge f7cd976, CI https://github.com/bebote0997/ai-market-system/actions/runs/38114705366. Revisión de Grok Bot: APPROVE.
- [x] PR #31: PNG con matplotlib desde la evidencia en solo lectura y recuperación cada 60 s (https://github.com/bebote0997/ai-market-system/pull/31), merge 7acf384, CI https://github.com/bebote0997/ai-market-system/actions/runs/38117616896. Revisión de Grok Bot: APPROVE.
- [x] Las alertas están OFF por defecto (`AI_FLOOR_ALERTS_ENABLED`), fuera del trading path; hay tests de que un fallo de alerta no cambia el estado PAPER.
- [ ] **Diferido al pre-flight de la Fase 13:** F09-T18, el envío real de email y Slack desde Render (el box bloquea SMTP). Ver `phase-13-runbook.md`.
- [ ] **Diferido al pre-flight de la Fase 13:** con `AI_FLOOR_V2_POSITION_CATCH_UP` OFF, el gráfico sale NO_DATA (decisión del owner).

## PR #37 — setup-chart email listo con cache (2026-10-13)

`AI_FLOOR_SETUP_CHART_EMAIL` OFF por defecto. El worker reconcilia setups validados por páginas,
lee cierres 5m de cache en modo read-only al timestamp del setup y persiste los precios junto al evento.
Dedup por setup ID y recuperación sin releer cache conservados. PNG con matplotlib Agg existente,
Entry/SL/TP y labels compartidos (incluye contract_multiplier; sin multiplier, distancia de precio).
Sin Chrome/Kaleido; requisito Kaleido eliminado. Falta de cache → OHLC exact-cycle o texto;
fallo de rendering → email texto. No merge de `origin/v2/pre-demo-fixes` ni cambio del writer.

Tests offline con PNG real + SMTP mock: esquema/ruta default y explícita, read-only/hash,
cutoff/cierre/símbolo/límite, OFF no-op, dedup/restart con cache borrado, fallback y reconciliación
más de 200 setups. Suite enfocada dashboard/alertas: 58 PASS; cierre full-suite en CHANGELOG_AGENT.md.
F09-T18 (envío real desde Render) sigue diferido; sin activación, envío real ni deploy.

Cierre PR #37: suite completa 1629 tests OK (2 skipped, 1 expected failure), 190.416s.
Chromium CSS y app real local con fixtures: PASS; Ruff/diff-check: PASS.
