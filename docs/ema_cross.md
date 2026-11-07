# EURUSD EMA 10/21 cross (demo only, OFF)

Prepared 2026-11-07. Flag `AI_FLOOR_EMA_CROSS` stays **unset / not `1`**. Nothing here is merged, deployed, or activated. `REAL_EXECUTION_ENABLED` stays `False`. DEC-FX-SL-3 (EURUSD limit 12.5 pips, SL 25 / TP 50, partials and trail) is unchanged.

## What the flag does

Exact `AI_FLOOR_EMA_CROSS=1` on a process that already has OANDA practice routing lets the demo router watch a second EURUSD strategy. Any other value, including unset, does not create the signal table and does not read extra broker state for this strategy.

* M15 close, shift 0: EMA10 crossing up through EMA21 is LONG; crossing down is SHORT.
* Last completed H1 only: LONG needs EMA10 > EMA21, SHORT needs EMA10 < EMA21. A bar that has not closed yet is ignored.
* The cross is stored. The market order is eligible 15 minutes later, and only on the first evaluation inside the following 15 minutes.
* SL 20 pips and TP 40 pips are taken from the broker quote at that evaluation (ask for LONG, bid for SHORT).
* No partial, no breakeven, no trail. Friday 16:45 America/New_York and the one-week max hold still close the position.
* The order is tagged `EURUSD_EMA_CROSS`. The pullback manager only touches `EURUSD_H1_PULLBACK`. Reversal will not close an EMA position to open a pullback.
* One routed position or resting order blocks the other strategy. EMA does not reverse the pullback.
* The signal row lives in the broker state DB (`ema_cross_signals`). A restart reloads `PENDING` and will not send a second order for a `SUBMITTED`, `REJECTED`, `CANCELLED`, or `EXPIRED` id.
* Size is the router's existing rule: 1% of OANDA practice NAV, then `AI_FLOOR_BROKER_DEMO_MAX_UNITS` (default 100000). The risk engine is not bypassed.

## Validation language

The frozen note that travelled with this candidate reported a 2021-09 to 2026-10 simulation and called 2024–2026 a walk-forward holdout (fixed book: n=143, PF=1.304, net R=24.41, max DD=-8.0R on that run). Those years were already in the sample while the variant was being chosen. That pass is a **retrospective evaluation on reused data**. It is not an untouched holdout and it is not a walk-forward. It does not show that the strategy will make money, and it is not a reason to use real funds. No new optimization was run for this PR.

## Where the live demo is stricter than that simulation

The simulation is not re-run here. These differences are kept on purpose.

| Topic | Simulation note | This demo |
|---|---|---|
| News | No news filter | HIGH impact in the active window, macro `ERROR`, or a missing/broken macro report blocks the entry. An empty calendar (`NO_DATA`) does not, which is the same rule the setup validator already uses. |
| Size | Fixed 100000 units | 1% of NAV, then the existing unit cap. The cap can match 100000 units; a smaller NAV sizes down. |
| Fill | First M5 open, long at ask, short at bid, same-bar stop first | Broker market order at the quote when the router evaluates inside the 15-minute slot. Stop and target are still 20/40 from that quote. There is no bar-open fill model and no same-bar stop-first assumption. |
| Session | UTC [12, 15) | The confirming M15 close **and** the entry time must both fall in UTC [12, 15), on a weekday, inside London or New York (the global session clock), and outside the Friday 16:45 New York entry ban. A cross whose entry would land at 15:00 UTC is not armed. |
| Other book | One position for this candidate | Also waits if the pullback (or any routed order) is open. It does not close that position. |
| Exits | Fixed stop and target until filled | Same, plus the global Friday flatten and the one-week max hold. The optional Day-1 breakeven is not applied to EMA rows. |

## Activation and rollback

Activation is a later decision. It needs the practice account flat, `AI_FLOOR_EMA_CROSS=1` set only with an approved deploy, and routing already on. This PR does not change Render.

Rollback: unset the flag or set it to `0`, and deploy that config. Open EMA rows keep their tag, so they still flatten on the week rule and still do not receive pullback partials. New crosses are ignored. The pullback flag is independent.

## Tests

`test_ema_cross.py` covers the cross, the H1 filter, the 15-minute wait, a missed slot, an opposite cross, an H1 flip, the 15:00 UTC boundary, the weekend, the news block, a busy book, restart idempotency, strategy tags, and the refusal to trail or reverse an EMA position.
