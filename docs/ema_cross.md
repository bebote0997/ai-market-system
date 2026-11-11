# EURUSD EMA 10/21 cross (demo only, OFF)

Prepared 2026-11-07. Flag `AI_FLOOR_EMA_CROSS` stays **unset / not `1`**. Nothing here is merged, deployed, or activated. `REAL_EXECUTION_ENABLED` stays `False`. DEC-FX-SL-3 (EURUSD limit 12.5 pips, SL 25 / TP 50, partials and trail) is unchanged.

## What the flag does

Exact `AI_FLOOR_EMA_CROSS=1` on a process that already has OANDA practice routing lets the demo router watch a second EURUSD strategy. Any other value, including unset, does not create the signal table and does not read extra broker state for this strategy.

* M15 close, shift 0: EMA10 crossing up through EMA21 is LONG; crossing down is SHORT.
* Last completed H1 only: LONG needs EMA10 > EMA21, SHORT needs EMA10 < EMA21. A bar that has not closed yet is ignored.
* The cross is stored. The market order is eligible 15 minutes later, and only on the first evaluation inside the following 15 minutes.
* The market order is sent with SL 20 / TP 40 attached to the quote (ask for LONG, bid for SHORT) so the order is not sent naked. After the fill, the broker's own stop and target are read. `ANCHORED` is stored only when that read shows both prices at 20/40 from the broker fill. A missing read is retried. A missing or non-protective price is replaced, and a failed replace closes. A failed close stays open and is retried. The local quote prices are not treated as the broker's protection.
* No partial, no breakeven, no trail. Friday 16:45 America/New_York and the one-week max hold still close the position.
* The order is tagged `EURUSD_EMA_CROSS`. The pullback manager only touches `EURUSD_H1_PULLBACK`. Reversal will not close an EMA position to open a pullback.
* One routed position or resting order blocks the other strategy. EMA does not reverse the pullback.
  Exact `AI_FLOOR_LIMIT_MANAGEMENT_V2=1` on the practice router is the exception for a resting pullback LIMIT only: an EMA entry cancels that limit and then sends. An actually filled position still blocks. Unset or any other value keeps the block. The flag is off by default and is not an EMA exit change.
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
| Fill | First M5 open, long at ask, short at bid, same-bar stop first | The order is sent against the quote. After the actual fill is known, SL and TP are replaced to 20/40 from that fill. There is no bar-open fill model and no same-bar stop-first assumption. |
| Session | UTC [12, 15) | The confirming M15 close **and** the entry time must both fall in UTC [12, 15), on a weekday, inside London or New York (the global session clock), and outside the Friday 16:45 New York entry ban. A cross whose entry would land at 15:00 UTC is not armed. |
| Other book | One position for this candidate | Also waits if the pullback (or any routed order) is open. It does not close that position. |
| Exits | Fixed stop and target until filled | Same, plus the global Friday flatten and the one-week max hold. The optional Day-1 breakeven is not applied to EMA rows. |

## Actual fill, not the quote

The quote is only the price used to attach a stop and target at submission. It is not the fill.

| What happened | What the demo does | Guarantee |
|---|---|---|
| Actual fill is already within half a pip of 20/40 | Leave the attached prices. `FILL_VERIFIED`. | The distances match. |
| Fill slipped and the stop is more than 20.5 pips away, or is not protective | Replace SL and TP to exactly 20/40 from the fill. | Exact only if the broker accepts the replace. |
| That replace is rejected or unknown | Close the trade, reason `EMA_RISK_UNCORRECTED`. | The oversized risk does not stay open. |
| The live bid/ask is already through the corrected 20-pip stop | Close immediately, reason `EMA_PRICE_THROUGH_STOP`. No replace is sent. | The position is not left past its risk. |
| Fill is better, so the attached stop is tighter than 20, and the replace fails | Keep the tighter stop. `EMA_FILL_KEPT`. | Risk stays under 20 pips. Exact 40 is best-effort. |
| Fill price not known yet | Keep the quote-based SL/TP and retry. `FILL_CHECK_UNAVAILABLE`. | The trade is protected, but 20/40 from the fill is not confirmed. |

This does not use the pullback rule that refuses to widen a stop. That rule would freeze a quote-based stop that is no longer 20 pips from the fill. The replace runs only for rows tagged `EURUSD_EMA_CROSS`.

## Activation and the unchanged Render key set

The running Render environment is not changed by this PR. `AI_FLOOR_EMA_CROSS` is a **new name**. It is not part of the current 42-key set (name hash `27efb336cec1008d`). Deploying this code without adding that name leaves the strategy off, which is the safe default. Turning it on requires the owner to approve adding the name `AI_FLOOR_EMA_CROSS` with the value `1`, on a flat practice account, with an explicit deploy. The feature cannot be activated while that key-name set stays unchanged. No key is added or edited here.

## Activation and rollback

Activation is a later decision. It needs the practice account flat, `AI_FLOOR_EMA_CROSS=1` set only with an approved deploy, and routing already on. This PR does not change Render.

Rollback: unset the flag or set it to `0`, and deploy that config. Open EMA rows keep their tag, so they still flatten on the week rule and still do not receive pullback partials. New crosses are ignored. The pullback flag is independent.

## Tests

`test_ema_cross.py` covers the cross, the H1 filter, the 15-minute wait, a missed slot, an opposite cross, an H1 flip, the 15:00 UTC boundary, the weekend, the news block, a busy book, restart idempotency, strategy tags, and the refusal to trail or reverse an EMA position.
