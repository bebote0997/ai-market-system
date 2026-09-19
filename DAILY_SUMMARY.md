# DAILY_SUMMARY — supervisión PAPER

El callback existente produce un resumen del **día UTC anterior completo**. Por
ejemplo, la primera llamada del 19 de septiembre resume el 18, evitando congelar
el conteo con el primer ciclo del día. No cambia la cadencia ni las sesiones.
No recupera automáticamente días anteriores a ese día si el proceso estuvo apagado.

Solo lee SQLite dentro de una transacción `BEGIN IMMEDIATE`. Journal y payload de
notificación se confirman antes de reclamar la entrega Slack. El journal identifica
el día con `daily:YYYY-MM-DD`; llamadas concurrentes, reinicios y fechas fuera de
orden no crean un segundo resumen. La entrega conserva la política existente de
un máximo de un intento: timeout/HTTP 429/5xx quedan `FAILED`, sin reintento que
pueda duplicar mensajes. Un crash tras reclamar el intento puede dejarlo sin enviar.

## Definiciones

- `cycles`: runs iniciados en el día UTC, incluidos fallidos o aún en curso,
  excluyendo `SESSION_SKIPPED` tanto por estado final como por journal (el runtime
  histórico persiste esos skips con final_status NO_DATA). Cada slot persistido
  cuenta una sola vez.
- `cycles_by_symbol`: mismo conteo por símbolo; incluye ceros para los símbolos
  habilitados persistidos. `NO_SETUP`, `WATCH`, `VALID_SETUP` proceden de `setups`;
  `RISK_REJECTED` de `risk_decisions`, para esos runs. VALID_SETUP puede coexistir
  con rechazo de riesgo; no se deriva del estado final de IA.
- `paper_orders_created`: órdenes cuyo `as_of` pertenece al día.
- `positions_opened`: posiciones cuyo `opened_at` pertenece al día, aunque cerraran.
- `positions_closed`: trades con `exited_at` en el día.
- `realized_pnl_day`: suma de `net_pnl` de esos cierres, después de costes;
  cero sin cierres, null si a alguno le falta PnL. No usa el acumulado de cuenta.
- `positions_open_current`, `unrealized_pnl_current`, `equity_current`: última
  instantánea persistida al generar el mensaje, no reconstrucción histórica ni
  precios recalculados. Cuenta identificada por `system_state.account_id`.
- `provider_problems`: eventos primarios de problemas de datos/proveedor;
  incluye datos ausentes/stale, autenticación, entitlement, rate limit,
  configuración y respuestas/barras inválidas. Excluye DATA_CHECK y RUN_FAILED
  redundantes. `errors` cuenta aparte todos los eventos de severidad ERROR;
  ambos conteos pueden solaparse y no deben sumarse.
- `macro_high_events`: IDs únicos de MACRO_HIGH_IMPORTANCE detectados ese día,
  deduplicados entre símbolos. `macro_policy_high_events` separa relevancia HIGH
  por política interna de importancia HIGH declarada por el proveedor.
- `runner_status`, `scheduler_status`, `heartbeat_utc`: estado persistido al
  capturar. Runner guarda RUNNING al inicializar y STOPPED al cerrar; un crash
  puede dejar un estado antiguo, por eso se incluye heartbeat. Bases anteriores
  sin estado de runner devuelven null. `last_cycle_status` y
  `last_cycle_final_status` conservan el último resultado durable.

Contadores sin registros son 0; valores de cuenta/estado desconocidos son null.
`snapshot_at_utc` identifica el momento de captura, no garantiza frescura de precios.
No se llama a IA ni a providers para elaborar o enviar el resumen.

## Mensaje exacto: ejemplo de prueba controlada, sin actividad

Slack recibe un objeto `text` con esta cabecera y el siguiente JSON en una sola
línea (las claves se ordenan alfabéticamente; aquí se indenta para facilitar lectura).
Los valores reales procederán de SQLite.

```text
AI Market System PAPER DAILY_SUMMARY
```

```json
{
  "NO_SETUP": 0,
  "RISK_REJECTED": 0,
  "VALID_SETUP": 0,
  "WATCH": 0,
  "cycles": 0,
  "cycles_by_symbol": {},
  "date_utc": "2026-09-18",
  "equity_current": null,
  "errors": 0,
  "heartbeat_utc": null,
  "last_cycle_final_status": null,
  "last_cycle_status": null,
  "macro_high_events": 0,
  "macro_policy_high_events": 0,
  "paper_orders_created": 0,
  "positions_closed": 0,
  "positions_open_current": 0,
  "positions_opened": 0,
  "provider_problems": 0,
  "realized_pnl_day": 0,
  "runner_status": null,
  "scheduler_status": null,
  "snapshot_at_utc": "2026-09-19T00:00:00+00:00",
  "unrealized_pnl_current": null
}
```

Validación: 9 tests específicos nuevos; suite completa 489 tests OK sobre la base
committed f3af0c3. Transporte falso para éxito, timeout, conexión, HTTP 429/5xx y
excepción inesperada; comprobación de commit con segunda conexión SQLite,
reinicio, concurrencia, fallo entre journal y captura, UTC y más de 500 eventos.
No se envió ningún mensaje real ni se activó runner/scheduler/experimento.
