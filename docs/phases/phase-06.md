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
La llamada a run_floor es byte-idéntica a origin/main: el flag no selecciona otro planner.
Solo una posición OPEN del mismo símbolo sin orden pendiente entra en execute; flat y pendientes
conservan el submit/gate certificado y sus eventos, incluido PAPER_ORDER_SUBMITTED. El contrato
fixed-3R sigue siendo obligatorio en candidate/Risk V2: los planes V1 del runtime se rechazan
con not_a_fixed_3r_plan antes de cerrar; no se relaja el contrato para activar reversión.
Mismo setup exige misma dirección y |entry nuevo − entry posición| ≤ 0.25 ×
|entry posición − stop original| (bordes inclusivos). Stop original viene de la orden de origen,
nunca del SL actual; orden ausente → NO_DATA para similitud. Setup_id igual nunca convierte
una dirección opuesta en mismo setup. Fuera de la zona, misma dirección sigue bloqueada.

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

## DEC-14.1 — review fixes locales, 2026-10-11

Tests Phase 4 restaurados exactamente desde origin/main. Gate Phase 8 permite únicamente
el método close_market exacto de DEC-14.1 y compara todos los demás bytes del broker con P3.
No cambia certificación ni activa flags.

Excepción del fingerprint de `storage/database.py` en `test_system_health.py`: además de
las exclusiones B2.1 existentes, se normaliza únicamente el fragmento
`"POSITION_CLOSED", "STRUCTURE_REVERSE_SUBMITTED", "DAILY_SUMMARY",` a
`"POSITION_CLOSED", "DAILY_SUMMARY",` y se eliminan exactamente estas cuatro líneas:

```python
                if row["event_type"] == "STRUCTURE_REVERSE_SUBMITTED":
                    execution = json.loads(row["payload"])
                elif row["event_type"] == "POSITION_CLOSED" and json.loads(row["payload"]).get("exit_reason"):
                    execution = json.loads(row["payload"])
```

Cada fragmento debe aparecer exactamente una vez. Permite seleccionar la alerta reverse y
entregar geometría/exit_reason al mapper Phase 9, sin cambiar schema ni escrituras económicas.
Todo otro byte LF-normalizado sigue fijado al SHA256 frozen V1
`80f8c9ae037bb27dbcd45403ea33655b07dfed3983f3cb6637d8704e5ef4ebde`.

Investigación replay offline: el corpus no puede regenerarse sin las barras originales.
`replay/acquire.py` necesita red y key Twelve Data; `lab_run.py` reconstruye PKL solo a partir
de una DB existente. El origen documentado es `data/replay/replay_twelve_data_12m.db`,
Twelve Data Basic sin pago, 2025-10-06 → 2026-10-06 (docs/archive/V2_PHASE4_TRADE_PLANNER.md).
No existe esa DB, market_replay.db, ni lab_*.pkl/copia de barras local; fixtures sintéticas
no reemplazan evidencia histórica. CLI ejecutada: NO_DATA; detalle exacto en
`docs/replay/dec14_impact.txt`, sin números fabricados.

Validación review: ruff PASS; tests focalizados 115 passed / 135 subtests. Suite pytest completa:
1541 passed, 5 failed (dashboard fixture temporal preexistente), 1 xfailed ISSUE-012,
1100 subtests passed (175.10 s). Gate histórico broker corregido, sin otro fallo.
