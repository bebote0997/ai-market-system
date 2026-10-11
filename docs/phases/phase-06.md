# Fase 6 — Multi-Setup / Position Conflict Engine

**Estado:** CERTIFIED + MERGED

**Evidencia:** PR #11 MERGED. HEAD `6a222fdc565b496d9142e78e492165b6bb26bb42` → main `405df6a8b18172cd94da23b9e48380a4405ba8cd`. CI 37646196104 SUCCESS. P6.2E PASS. EVID-021.

**Alcance / notas:** T02/T04/T05 N/A (DEC-6.2 A). Sin pyramiding/hedging/netting (DEC-6.3/6.4 A). Rollback MEDIUM carry-forward; compatibility gate obligatorio antes de runtime.

Fuente: Libro Maestro v1.9 (histórico) y repositorio.

## DEC-14.1 — implementación local, 2026-10-11

El owner Jeferson Tejeda aprobó a las 10:00 ET la política opcional `STRUCTURE_CHANGE`; supersede
DEC-6.5 A / DEC-6.6 A únicamente en runtime cuando el flag esté ON. `classify()` permanece puro y
sin cambios. `BLOCK` conserva planner, fingerprint, journal y comportamiento legado.

`execution/opposite_policy.py` reutiliza candidate_identity, validación de conflict_path,
Risk V2/prepare_reservation, lock del slot, CAS de todo el estado PAPER y guards halt/REX.
Con flag ON, el planner fixed-3R existente produce el contrato requerido por Risk V2; las nuevas
órdenes (también cuando ya está flat) usan reservas V2 con identidad/política estampadas y fill normal.
La zona similar se define conservadoramente como ±un incremento de precio respecto al entry real;
fuera de ella, misma dirección sigue bloqueada. No se permite pyramiding.

BOS: evidencia determinista del setup 15m, dirección opuesta, break posterior a la apertura,
bar alineado y cerrado (`start + 15m <= now`), no más viejo que el límite existente de frescura 15m;
close comprobado contra la barra original y estrictamente más allá del SL actual. Nunca se infiere
un cambio por setup_id diferente. En pérdida (incluye cero), estructura 1H intacta significa precio
actual del lado correcto del SL, estado 1H no contrario y sin BOS 1H contrario posterior a la apertura.
Todos los timeframes deben tener datos frescos y barras ya cerradas. IA CAUTION o revisión no saludable
bloquean el cierre. SL/TP management habitual sigue teniendo precedencia; pendientes conservan su ciclo.

Cierre PAPER a último close 5m fresco con `EXIT_STRUCTURE_CHANGE`, coste una sola vez y equity MTM.
Cierre + `DEC14_CLOSE` se guardan juntos antes del nuevo Risk V2. La misma protección por símbolo abarca
ambos saves; Risk usa la equity durable posterior al cierre. Rechazo deja la cuenta flat y se registra.
Close emite `POSITION_CLOSED`; reverse aprobado emite `STRUCTURE_REVERSE_SUBMITTED`, mapeado a
`ORDER_SUBMITTED` por la tubería de alertas F9 con cantidad/entry/SL/TP efectivos. Posición solo se abre
por el fill existente, nunca simultáneamente con la anterior.

**Restart determinista:** no reanudar la reversión. Cierre sin decisión terminal se registra como
`REVERSE_ABANDONED_AFTER_RESTART` tras recovery; tampoco un retry del mismo run puede reabrir.
La recuperación no abandona un run aún RUNNING de otro proceso ni toma su lock; debe primero quedar
interrumpido/recuperado por el procedimiento existente. Claim, close y decisión terminal son durables;
rerun conserva la decisión y un candidato distinto para el mismo run se rechaza.

Flag inválido → BLOCK + warning de log/journal y health `opposite_setup_policy_health=DEGRADED`.
Implementación ≠ certificación/activación. Replay: `python -m replay.opposite_policy_impact
<market_replay.db> <p41a_lab_dir>`, salida en `docs/replay/dec14_impact.txt`. Corpus original ausente
en este workspace: resultados **NO_DATA**, sin conteos inventados; comparación económica pendiente.
