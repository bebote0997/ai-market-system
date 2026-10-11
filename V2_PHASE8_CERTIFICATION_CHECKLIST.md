# V2 Phase 8 — G-8.INT certification checklist (P4b preparation)

- **Status:** PREPARATION ONLY. No A2 period exists; nothing below has been measured on a real period.
- **Tool:** `python -m replay.g8int` (read-only; backup-API copies). Classes: PASS / FAIL / NOT VERIFIED / BLOCKED.
  Absence of errors is never PASS; synthetic test periods never close HIGH-8.1 or M-5.
- **Global verdict:** BLOCKED until every row is PASS, including the rows that depend on Owner acts, external evidence
  and independent review.
- **Safety:** PAPER ONLY · REAL EXECUTION DISABLED · NAS100 OFF · V2 flags OFF · PHASE 8 BLOCKED · HIGH-8.1 OPEN ·
  M-5 OPEN · B-STRICT PROVISIONAL.

## 1. G-8.INT criteria (design section 5)

| # | Criterion | Evidence source | Tool / check | Needs beyond the code |
|---|---|---|---|---|
| G1 | Scope A (all enabled symbols in scope) | REX run identity | `g8int` G1 | the A2 configuration |
| G2 | Simulation on copies (section 6) | Owner copies, export, frozen clock | `g8int --simulation-record` (`V2_SECTION6_SIMULATION/1`: PASS, no STOP, input hashes, bound to the deployed commit) | **BLOCKED** until the Owner's rehearsal record exists (OP-1) |
| G3 | ≥ 10 counted session days per symbol | `runs`, scheduler sessions, halts | `g8int` G3 | **BLOCKED:** a live A2 period |
| G4 | ≥ 200 cycles per symbol, ≥ 1 position under catch-up | `runs`, REX ST2C writes | `g8int` G4 | live A2 period |
| G5 | ≥ 5 reconciled closes per symbol (20-day extension) | REX ST2C + G14 + Evidence Store digests | `g8int` G5 | live A2 period |
| G6 | 0 duplicate fills, closes, claims | PAPER tables, `runs` | `g8int` G6 | — |
| G7 | `EVIDENCE_UNAVAILABLE` ≤ 1 % of in-scope cycles | journal, REX | `g8int` G7 | live A2 period |
| G8 | 0 unexplained evidence GAPs | Evidence Store anomalies | `g8int` G8 (any GAP → NOT VERIFIED) | Owner explanation records |
| G9 | Revision gate CLEAR | `revision_review.demo_gate` | `g8int` G9 | Owner reviews if anomalies |
| G10 | 0 ERROR runs, 0 halts in the window | `runs`, `HALT_OBSERVED` | `g8int` G10 | — |
| G11 | Chain matches external anchors | anchors (OP-6), OAR-G, OAR-A | — | **BLOCKED:** external anchors |
| G12 | Safety on every run | runs, schema, `REAL_EXECUTION_ENABLED` | `g8int` G12 | — |
| G13 | Independent review PASS | reviewer | — | **BLOCKED:** Copilot + Owner |
| G14 | Every run reproduces from REX | REX | `rex_oracle` via `g8int` | — |
| G15 | Whole-period EDG chain VERIFIED | REX + EDG + external evidence | `rex_chain` via `g8int` | single-writer attestation, OAR-G, anchors |

## 2. Integration checklist (Owner, 10 points)

| # | Point | Tool evidence | Closure needs |
|---|---|---|---|
| I1 | Code identity and baseline | `run_metadata.git_commit`, REX rule identity; `freeze_amendment` (I-C6) | deployed `Y_P2` |
| I2 | Genesis integrity | `genesis_verifier` with the Owner-signed OAR-G | OP-7 signature |
| I3 | EDG / psh integrity | `rex_chain` (SQLite scope, over evidenced writes) | G11 / G15 external evidence |
| I4 | REX chain and G14 / G15 | `rex_oracle`, `rex_chain` | — |
| I5 | R-HALT and recovery | `halt_verifier` | **M-5:** independent certification |
| I6 | Per-symbol catch-up | REX ST2C per symbol, G5 | **HIGH-8.1:** live two-symbol A2 evidence |
| I7 | Multi-Setup Conflict Engine compatibility | suite + inventory (`conflict_path` / `risk_reservation` unwired, unchanged) | CI on the exact SHA |
| I8 | Risk, sizing, SL/TP, broker unchanged | G14 reproduction; unchanged-file check | — |
| I9 | Rollback and restart | seal checks per start (I-G18); legacy rollback documented | — |
| I10 | Tests, CI, external evidence | CI on the SHA; external evidence bundle | reviewer |

## 3. A2 preparation tools (no real A2 is created)

| Tool | Purpose | Design |
|---|---|---|
| `replay/archive_anchor.py` | P1 archive by backup API, flat proof, unsigned OAR-A body | 3.11.1 items 4, 6; NC11, NC12 |
| `replay/a2_readiness.py` | pinned path, no symlink, nlink 1, pairwise distinct, OAR-A / archive hash, genesis-only first start | 3.11.1 items 2, 5; I-C5, I-C7; NC5–NC9 |
| `replay/freeze_amendment.py` | `Y_P2` touches only the baseline pin and `EXPERIMENT_FREEZE_P2.md` | 3.11.1 item 1; I-C6; NC10 |
| `runtime/genesis_prepare.py`, `replay/genesis_anchor.py` | sealed genesis of the new P2 DB (P2b) | 5.1.3.2 |
| `replay/g8int.py` | the matrix above | section 5 |

**Reconciliation (design text, not a new decision):** 3.11.1 item 2 says the P2 trading DB "must not exist before the
first P2 start"; 5.1.3.2 (later revision) creates it in GENESIS_PREPARATION before OAR-G. `a2_readiness` therefore
requires, at the first start, a P2 DB with genesis only (one `GENESIS_PREPARED`, no `EXPERIMENT_STARTED`, no runs); the
genesis tool's create-exclusive enforces freshness.

## 4. Owner acts still required (none performed)

- DEC-8.18 (status verification, as-is: axes, H5) and DEC-8.19 (boundary handling, option (a): flat precondition under A2, no inherited positions/orders) APPROVED 2026-10-10 by the Owner (Jeferson Tejeda / bebote0997). The DEC-8.18 procedure has not been executed yet (needs an Owner DB copy and a signed H5 attestation).
- `EXPERIMENT_FREEZE_P2.md` and the signed `Y_P2` (on Owner instruction).
- H5 attestation, OAR-A (archive) and OAR-G (genesis) signatures — OP-7.
- Chain-head anchors (OP-6) and the single-writer attestation.
- Operational authorization for the A2 start (8.3); no E0 until then.
