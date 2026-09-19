# Demo Runner PAPER — Fase 7

`runtime.demo_runner.DemoRunner` coordina `Scheduler`, `OperationalRuntime`, Twelve Data, los scouts deterministas, el validador, el planificador, Risk Engine, OpenAI, la política final, Paper Broker, Trade Manager, SQLite y el journal. El scheduler permanece deshabilitado por defecto. Solo XAUUSD y EURUSD están habilitados; NAS100 sigue soportado y deshabilitado. Massive sigue implementado y no activo.

## Autoridad y seguridad

- Cada slot de 15 minutos y símbolo obtiene un `run_id` UUID estable antes de consultar proveedores. SQLite reclama el slot y el lock por símbolo en una transacción. Un segundo tick del mismo slot devuelve `DUPLICATE` sin otra orden.
- La IA solo devuelve `AIResponse` validado contra identidad, contrato y evidencia. El plan, sizing, equity, SL/TP, `RiskDecision` y broker pertenecen a Python. `paper_policy` exige `PLAN_READY`, `APPROVED` y revisiones IA sanas. El broker comprueba nuevamente multiplicador, riesgo máximo de 1%, R:R mínimo 1:3 y la próxima barra cerrada.
- `dry_run=True` bloquea `TradeManager`, fills y órdenes PAPER, y registra `PAPER_DIAGNOSTIC_BLOCKED` si surge una oportunidad. `diagnostic_outside_session=True` solo funciona con ese modo; permite comprobar infraestructura fuera de la ventana de análisis sin autorizar operaciones.
- Nunca existe una ruta de ejecución real. El catálogo de instrumentos sigue sin multiplicadores contractuales live; las fixtures de tests suministran multiplicadores sintéticos para verificar el flujo PAPER.

## Estado durable

SQLite schema 2 añade `review_reports` y `notification_events` a schema 1 mediante migración transaccional. `ReviewReport` versionado persiste estado final, setup, resúmenes de especialistas, IDs de evidencia recibida y citada, validación, decisión de riesgo, IDs de órdenes/fills/trades, equity/PnL y error seguro. No guarda razonamiento privado. Ciclos sin datos y fallidos también tienen un informe. Un reinicio marca slots abandonados como fallidos, libera locks y conserva cuenta, órdenes, posiciones y journal.

Eventos seleccionados del journal se convierten en `NotificationEvent` versión 1.0 con ID estable por fila de journal. La persistencia ocurre antes de `NotificationSink.deliver`. Hay `NullNotificationSink` y `FakeNotificationSink`; un fallo de entrega no cambia la operación. `NO_SETUP` ordinario permanece en journal sin notificación. Eventos incluyen fallos y recuperación, setup válido, rechazo de riesgo, apertura/cierre PAPER y resumen diario. La UI solo abre la base en modo lectura y muestra salud, readiness, último ciclo, cuenta, revisión y notificaciones.

## Preflight y diagnóstico

`preflight` verifica configuración PAPER, ejecución real deshabilitada, símbolos, proveedor activo, modelo, scheduler deshabilitado/cadencia, presencia de credenciales sin leerlas en la salida, integridad y escritura SQLite y experimento no iniciado. `READY` significa preparación local; la salud real de OpenAI/Twelve Data se comprueba en el diagnóstico live. La prueba no arranca el scheduler ni el experimento. Ninguna clave se almacena en SQLite ni en documentación.
