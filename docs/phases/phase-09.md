# Fase 9 — Email Notification Engine

**Estado:** IN PROGRESS (issue #20, rama `v2/phase9-alerts`). Implementado; no revisado, no certificado, no mergeado, no activado.

**Tareas (Libro v1.9):** VALID/EXECUTED TRADE; ORDER SUBMITTED; POSITION OPENED; POSITION CLOSED; RISK REJECTED; AI CAUTION; PROVIDER FAILURE; SYSTEM DEGRADED; SYSTEM CRITICAL; RECOVERY; contenido completo de alerta de trade (incl. Chart, Risk %, Session, Quantity — ISSUE-007); chart e identidad visual; diseño luxury/institutional/mobile; deduplication; delivery status; email failure detection; retry policy; test emails; alert priority (F09-T01…T19)

**Gate:** requiere fase anterior CERTIFIED y autorización explícita del owner.

**Implementación:**
- `runtime/alert_catalog.py` — catálogo de los 10 eventos con prioridad, contenido de alerta de trade (valores del llamador tal cual; ausentes = `NO_DATA`), mapeo desde el contrato durable `NotificationEvent`.
- `runtime/alert_render.py` — una plantilla HTML responsive (oscura/dorada, sin imágenes remotas, valores escapados) + texto plano.
- `runtime/alert_channels.py` — Email Gmail SMTP (`smtplib.SMTP_SSL` `smtp.gmail.com:465`, `GMAIL_APP_PASSWORD`; remitente/destinatario `AI_FLOOR_ALERT_EMAIL_FROM`/`_TO`, por defecto yefersontejeda@gmail.com) y Slack (`SLACK_WEBHOOK_URL`, reutiliza `runtime/notifications.py`).
- `runtime/alert_dispatch.py` — dedup por `event_id` y estado de entrega durable por canal (ledger SQLite), retry con backoff exponencial acotado, detección de fallos por canal; **OFF por defecto** (`AI_FLOOR_ALERTS_ENABLED` debe ser exactamente `1`); nunca lanza excepciones.

**Invariantes:** no está conectado a ningún camino de órdenes/trading (test estructural); un canal que falla no cambia órdenes/posiciones (test con estado PAPER real); tests solo con mocks (`smtplib.SMTP_SSL` y `urlopen` parcheados; cualquier `socket.create_connection` hace fallar el test). Sin cambios en estrategia/riesgo/broker, flags por defecto ni Render.

## F09 → tests (`test_phase9_alerts.py`)
| ID | Requisito | Tests | Cobertura |
|---|---|---|---|
| F09-T01…T10 | Los 10 eventos (VALID/EXECUTED TRADE … RECOVERY) | `TestCatalog::test_f09_t01_t10_ten_events_with_priority`<br>`TestCatalog::test_maps_existing_durable_notification_contract` | COVERED |
| F09-T11 | Contenido completo de alerta de trade (símbolo, lado, entry, SL, TP, cantidad, R:R, Risk %, sesión, run_id, setup_id, tiempos) | `TestCatalog::test_f09_t11_trade_content_complete`<br>`TestCatalog::test_f09_t11_absent_values_are_no_data_never_invented`<br>`TestCatalog::test_f09_t11_values_reported_verbatim_and_snapshotted` | COVERED |
| F09-T12 | Chart e identidad visual | `TestRender::test_f09_t12_t13_responsive_html_escaped` — identidad en plantilla; el campo Chart muestra la referencia del llamador o `NO_DATA`. No se genera imagen de chart. | PARTIAL |
| F09-T13 | Diseño luxury/institutional/mobile | `TestRender::test_f09_t12_t13_responsive_html_escaped` | COVERED |
| F09-T14 | Deduplication | `TestDispatcher::test_f09_t14_t15_dedup_and_durable_status`<br>`TestCatalog::test_event_id_deterministic` | COVERED |
| F09-T15 | Delivery status | `TestDispatcher::test_f09_t14_t15_dedup_and_durable_status`<br>`TestDispatcher::test_not_configured_channel_is_not_claimed` | COVERED |
| F09-T16 | Email failure detection | `TestDispatcher::test_f09_t16_failure_detection`<br>`TestEmailChannel::test_f09_t16_errors_typed_and_secret_free`<br>`TestDispatcher::test_f09_t17_retry_exhaustion_and_non_retryable` | COVERED |
| F09-T17 | Retry policy | `TestDispatcher::test_f09_t17_bounded_backoff_retry`<br>`TestDispatcher::test_f09_t17_retry_exhaustion_and_non_retryable` | COVERED |
| F09-T18 | Test emails | `TestRender::test_f09_t18_test_alert_is_labelled`<br>`TestEmailChannel::test_sends_via_gmail_smtp_ssl_with_html_and_text` (mock) | PARTIAL |
| F09-T19 | Alert priority | `TestDispatcher::test_f09_t19_priority_order`<br>`TestCatalog::test_f09_t01_t10_ten_events_with_priority` | COVERED |
| Invariante | Fallo de alerta no crea/altera órdenes; OFF por defecto; no cableado | `TestTradingIsolation::*`<br>`TestDispatcher::test_off_by_default`<br>`TestDispatcher::test_env_example_and_render_keep_alerts_off`<br>`TestDispatcher::test_dispatch_never_raises` | COVERED |

PARTIAL: T12 (generar la imagen del chart necesita una decisión del owner sobre la fuente/render; hoy solo se transmite `chart_ref`); T18 (`make_test_alert` produce un TEST etiquetado y se valida con mocks; un envío real de prueba requiere `GMAIL_APP_PASSWORD` y autorización explícita del owner).

**Pendiente para certificar:** revisión independiente, decisión sobre T12/T18 y sobre el cableado (dónde y cuándo invocar el dispatcher fuera del camino de trading), aprobación del owner.
