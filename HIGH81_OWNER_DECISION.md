# F08 / HIGH-8.1 — Owner decision and local correction

Date: 2026-10-10. Inspected base: `v2/phase8-p4b`,
`720f4b4d410e19c723dcaaefaad34aab337f3fe5`. Local changes, not committed or deployed.

## Decision

The Owner explicitly authorized replacing the OFF route for EURUSD and XAUUSD with the existing chronological
catch-up, solely to correct HIGH-8.1. This supersedes DEC-8.1 **only for the configuration proposed for Phase 8
certification**. The original DEC-8.1 record in `V2_PHASE8_EXECUTION.md` and `EXPERIMENT_FREEZE.md` are preserved.
It does not retroactively change the prior experiment, approve a new operational baseline, or certify Phase 8.

A2 and G-8.INT are separate operational follow-ups. Neither is activated by this decision or used as a prerequisite
for implementing and testing the local correction. No Libro Maestro, deployed flags or operational DBs changed.

## Correction and boundaries

- With `v2_position_catch_up=False`, EURUSD/XAUUSD supply their already-loaded 5m snapshot to `catch_up_position`.
  Existing normalization validates OHLC, symbol and duplicate timestamps before writes. Eligible closed bars after
  the durable position watermark are processed oldest first, bounded by the slot (start + 5m <= slot).
- The existing per-bar transaction, compare-and-save protection, ownership checks, halt gate and durable watermark
  remain authoritative. Restart resumes after the last committed bar. No new Evidence Store is created for OFF.
- ON retains committed Evidence Store behavior and failure policy. Explicit partial ON scopes retain DEC-8.14;
  symbols excluded from those scopes still use the legacy route. They are outside this certification proposal.
- Pending orders still fill only at the current gated bar, never retrospectively. Strategy, sizing, Risk Engine,
  Paper Broker and TradeManager are unchanged. Gap-open precedence and same-bar stop-first are unchanged.
- REX records snapshot management as chronological writes; its independent oracle recognizes the new explicit
  identity field while retaining support for historical records without it.

## Economic comparability

The old runtime could miss an intermediate SL/TP and leave a position open or close it later at a different price.
The corrected OFF route closes at the first eligible observed touch. Realized PnL, equity and consequently later
decisions can differ. Results must not be pooled with the frozen baseline; any operational baseline/start decision
remains with the Owner. This change does not repair previously advanced watermarks or rewrite historical trades.

The OFF route uses the snapshot supplied by the provider, not a durable historical market archive. Missing bars
outside that snapshot cannot be reconstructed, and no prices are invented. The existing ON Evidence Store remains
the route for durable market evidence and revision tracking. No provider, lookback window or data policy changed.

## Local evidence

`test_high81_runtime.py` covers both symbols, LONG/SHORT intermediate SL/TP, gap stops/targets, same-bar priority,
earlier-target precedence, unordered input, no lookahead, malformed intermediate data, restart before/after close
commit, duplicate cycles and pending-fill idempotence. Existing catch-up, concurrency and REX tests are reused.
Historical fixtures explicitly seed the pre-fix watermark when testing non-retroactive recovery; they no longer
require the corrected runtime to reproduce HIGH-8.1. Final test counts are recorded in `CHANGELOG_AGENT.md`.

PAPER ONLY / REAL OFF / NAS100 OFF. No activation, merge, deploy or Phase 8 certification.

## Changed files for review

- Code: `runtime/service.py`, `execution/position_catch_up.py`, `replay/rex_oracle.py`.
- New integration tests: `test_high81_runtime.py`.
- Existing tests/fixtures: `test_runtime_catch_up.py`, `test_phase8_catch_up_certification.py`,
  `test_phase8_activation_simulation.py`, `test_phase8_activation_preview.py`, `test_phase8_observe_only.py`,
  `test_phase8_rex_writer.py`, `test_phase8_rhalt_runtime.py`, `test_demo_runner.py`, `test_phase1_isolation.py`,
  `test_phase6_conflict_characterization.py`, `test_stale_safe_writers.py`.
- Decision/evidence: this document and `CHANGELOG_AGENT.md`.
- Pre-existing untracked `V2_PHASE8_OWNER_READINESS_PACKAGE.md` was preserved unchanged.
