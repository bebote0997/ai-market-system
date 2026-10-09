# V2 Phase 8 / P8.6 — Owner decisions and implementation handoff (DOCUMENTATION ONLY)

- **Baseline:** `f2f87be26822b130445ebcd47e31b14f20cca69d` (branch `v2/phase8-execution`).
- **Design reference:** `V2_PHASE8_P86_DESIGN.md` (working copy, revision P8.6F10). It is **uncommitted**; see blocker B-1.
- **Design status:** Copilot P8.6R11 → **P8.6 DESIGN COMPLETE — CONDITIONAL** (as relayed by the Owner).
  - Design completeness is **not** implementation or operational certification.
  - Phase 8 stays **BLOCKED**; HIGH-8.1 **OPEN**; M-5 **OPEN**; G14 / G15 specified, not certified.
- **Safety baseline:** PAPER only, REAL EXECUTION DISABLED, NAS100 OFF. Phase 9 not authorized.
- **Recorded:** 2026-10-08, the date the decisions were received in this session.

## 1. Master Roadmap ("Libro Maestro"): verification result (blocker B-2)

| Item | Finding |
|---|---|
| Expected location | The V2 docs refer to the "V2 Master Roadmap & Checklist (Word)" (`V2_HANDOFF_REQUIREMENTS.md:12`, `V2_PHASE1_SYSTEM_HEALTH.md:9`, `V2_PHASE2_MARKET_EVIDENCE.md:12`). It is not in the repository |
| Files found locally (read-only inspection) | `Downloads/AI_TRADING_FLOOR_V2_Master_Roadmap_Checklist.docx`: version 1.1, 2026-10-02, PHASE 0 CERTIFIED. `Downloads/AI_TRADING_FLOOR_V2_Master_Roadmap_Checklist_PHASE_5_CERTIFIED.docx` and its byte-identical copy `… (1).docx` (SHA-256 prefix `9bb117f750a56461`): **version 1.7**, official checkpoint 2026-10-06, "PHASE 0–1 CERTIFIED / CLOSED; PHASE 2–5 CERTIFIED / MERGED / CLOSED … Próxima: PHASE 6 — NOT STARTED" |
| Consistency with the repository | **Inconsistent.** Phase 6 (PR #11) and Phase 7 (PR #12, `main` 2753130) were merged after that checkpoint. Version 1.7 has no Phase 6 certification, Phase 7 or Phase 8 entry |
| Conclusion | The **current** version of the Libro Maestro cannot be verified from accessible files. **It was not edited.** No version number is invented. The decision entry below is a **provisional register** for the Owner to transfer into the verified current version |

## 2. Decision register (Owner decisions, recorded with their exact status)

| ID | Status | Scope (as approved) | Conditions / risks | Not implied |
|---|---|---|---|---|
| DEC-8.17 | **APPROVED POLICY** | **Measurement policy (design §8.1, original definition):** G-8.INT G1–G15 (G14 including the V1 fill gate; G15 stage replay); REX as the evidence source (5.1); multi-symbol semantics certified as-is (1.9.4); halt triggers; acceptance of the post-halt residual while R-HALT does not exist | *Correction 2026-10-08:* an earlier version of this register described DEC-8.17 as the "emergency stop policy". That was wrong. The emergency-stop principles (stop new admissions, preserve evidence, review and authorization before resuming = 3.8 H1–H7) belong to the 3.8 procedure and to **DEC-8.17b** | Not a certification; G14 / G15 not implemented |
| DEC-8.17b | **APPROVED POLICY** | Halt stop policy (3.8): Alternative 1 (admission contract) plus the post-halt persistence policy; with the 3.8 H1–H7 procedure, this carries the emergency-stop principles (stop new admissions, preserve evidence, review and authorization before resuming). The Owner **accepts** that one operation admitted before `T_h` may begin or commit its transaction after `T_h` | Residual: at most one economic write per process after `T_h`; M-5 stays OPEN until implementation and tests allow its closure to be assessed | No "zero effect after halt" claim; Alternative 2 (pinned-module change) not authorized |
| DEC-8.20 | **APPROVED POLICY** | A2: a new PAPER account of USD 10,000 for a new stage | Requires a verifiable P1 archive (OAR-A), a new DB, no inherited position or order, an approved baseline and freeze (`X_P2` / `Y_P2`, `EXPERIMENT_FREEZE_P2.md`), and a sealed genesis (OAR-G, SEALED_RUNTIME) | **No economic isolation between XAUUSD and EURUSD** (one shared account; design 1.9). Archive, genesis, transition and operation **NOT AUTHORIZED** |
| DEC-8.21b | **PROVISIONAL** (pending operational validation) | B-STRICT: REX / EDG / `psh` evidence; external chain-head anchors per UTC day with gaps ≤ 26 h; single-writer attestation; certification invalidated by any economic effect without mandatory evidence | Operational feasibility of OP-6 (daily anchors) and of the attestation is unproven; the stated limits (reverted alterations, consistent rewrite between anchors) remain | Not a certification rule in force until validated |
| Package plan | **APPROVED (planning only)** | Section 4 of this document | — | **Execution NOT AUTHORIZED** |
| DEC-8.13 | **APPROVED POLICY** (2026-10-08) | Scope variable set while the flag is OFF → error (§1.1) | — | No implementation authorized |
| DEC-8.14 | **APPROVED POLICY** (2026-10-08) | Observe-only ingestion (§1.5) and STRICT completeness (§1.7); busy ≤ 500 ms, DEGRADED after 4 consecutive failures, **subject to tests**; explicit acceptance of STRICT blocking and of the unknown provider lookback / entitlement | Parameters to be validated by the P1-B tests | No CALENDAR, no backfill, no activation |
| DEC-8.15 | **APPROVED POLICY** (2026-10-08) | Global conservative revision gate (§1.6) | — | — |
| DEC-8.16 | **APPROVED POLICY** (2026-10-08) | Mixed-path segments `TECHNICAL_ONLY`; B optional technical; A necessary; economic certification only if G-8.INT (DEC-8.17) passes (§1.9.3) | — | No certification |
| DEC-8.21 | **APPROVED POLICY** (2026-10-08) | LOW-1 fail-closed per §4 | — | No implementation authorized |
| DEC-8.18, 8.19 | **PENDING** | Unchanged from design section 8.1 | — | — |
| DEC-8.22-a…j | **NOT AUTHORIZED** | Implementation authorizations | — | — |
| OP-1…OP-7 | **NOT AUTHORIZED** | Operational acts | — | — |

**Consequences of the approvals:**
- **DEC-8.19** (boundary handling) was blocked on DEC-8.20. With A2 approved, its content reduces to "no inherited
  positions or orders (flat precondition)", but it **stays PENDING** until approved explicitly.
- **DEC-8.17** sets the G-8.INT measurement policy. **DEC-8.17b** sets the halt stop policy. Neither closes M-5.

## 3. Documentation state, before and after

| Aspect | Before | After |
|---|---|---|
| P8.6 design | REQUEST CHANGES cycles R…R10 | DESIGN COMPLETE — CONDITIONAL (P8.6R11, as relayed) |
| Owner decisions | DEC-8.13…8.22 all PENDING | 8.17 and 8.17b APPROVED POLICY; 8.20 APPROVED POLICY (conditional); 8.21b PROVISIONAL; the rest PENDING; 8.22 and OP NOT AUTHORIZED |
| Implementation | none | none (unchanged) |
| Phase 8 / HIGH-8.1 / M-5 | BLOCKED / OPEN / OPEN | BLOCKED / OPEN / OPEN (unchanged) |
| Libro Maestro | v1.7 found (Phase 5 checkpoint) | not edited (blocker B-2); this file is the provisional register |

## 4. Implementation packages (future; planning approved, execution NOT AUTHORIZED)

**Common rules for every package:**
- a dedicated branch from the committed design baseline;
- flags OFF by default;
- no activation, no deploy, no operational DB access;
- no change to the V1 strategy or trading decisions;
- `storage/database.py` is hash-pinned and is not modified (no package needs it under DEC-8.17b Alternative 1);
- the full suite plus CI before any commit;
- an independent review per package;
- the author never certifies its own work;
- every runtime change belongs to the new baseline `X_P2` (design 3.11.1) and is never hot-patched into a frozen
  period.

### P1: LOW-1 hardening and the technical per-symbol catch-up

| Field | Content |
|---|---|
| Design refs | Section 4 (LOW-1); section 1 (per-symbol contract: 1.1–1.7), 1.9.4 (sequence, documented only) |
| Potential files | `runtime/revision_review.py`; `runtime/config.py` (`v2_position_catch_up_symbols`, grammar, invariants, fingerprint, `catch_up_applies`); `runtime/service.py` (per-symbol branch, observe-only isolation 1.5); `runtime/cloud.py`, `runtime/demo_runner.py` (`catch_up_scope_valid`, `catch_up_evidence_contiguous` STRICT); `replay/activation_preview.py` (`--catch-up-symbols`); new tests |
| Dependencies | None on other packages. Policy (all APPROVED 2026-10-08): DEC-8.21 (LOW-1), DEC-8.13 / 8.14 / **8.15** / 8.16 (catch-up; DEC-8.15 governs §1.6). Authorization: DEC-8.22-a / b |
| Tests | Section 4.6 (LOW-1); matrix B C1–C9, F1, R1–R10, P1, V1, I1, S1; NE1–NE7; 1.5 isolation tests; OFF fingerprint and preflight byte-identical; inventory tests unchanged |
| Risks | Inventory-test substring constraints (F-8); the observe-only path leaking into EURUSD; fingerprint drift when OFF |
| Acceptance | All P1 tests pass; full suite and CI green; with the flag OFF: no behaviour or fingerprint change; flag ON with `XAUUSD` only: certification eligibility `TECHNICAL_ONLY`; independent review PASS |
| Rollback | Revert the P1 commits on the branch (nothing deployed) |
| Gate | DEC-8.13, 8.14, **8.15**, 8.16, 8.21 approved (**met**, 2026-10-08); DEC-8.22-a and DEC-8.22-b authorized (**not yet**); design committed (B-1, **not yet**) |

#### P1 split (2026-10-08): two sub-packages, separate commits and reviews

| | **P1-A: LOW-1** | **P1-B: technical per-symbol catch-up** |
|---|---|---|
| Policy | DEC-8.21 | DEC-8.13, 8.14, 8.15, 8.16 |
| Authorization | DEC-8.22-a | DEC-8.22-b |
| Files | `runtime/revision_review.py`; new `test_phase8_low1_review_validation.py` | `runtime/config.py`, `runtime/service.py`, `runtime/cloud.py`, `runtime/demo_runner.py`, `runtime/revision_review.py` (§1.6 gate scope only), `replay/activation_preview.py`; new `test_phase8_catch_up_scope.py`, `test_phase8_observe_only.py`, `test_phase8_scope_preflight.py` |
| Tests | §4.6 list | Matrix B C1–C9, F1, R1–R10, P1, V1, I1, S1; NE1–NE7; the §1.5 isolation tests (busy ≤ 500 ms timing, K = 4 → DEGRADED); NS1 (I-S1) |
| Acceptance | All §4.6 cases; history row count unchanged; existing revision tests unchanged | Flag OFF: fingerprint, preflight report and V1 behaviour byte-identical; a scope ≠ all enabled symbols → `TECHNICAL_ONLY`; EURUSD decisions and economics identical to a flag-OFF twin under the observe-only faults |
| Order | First (independent, smallest) | Second (after P1-A is reviewed; touches `revision_review.py` §1.6 after LOW-1) |

**Commands for each sub-package** (after authorization only):
1. `git switch -c v2/phase8-p1a` (or `v2/phase8-p1b`) from the documentation commit.
2. Run the focused tests.
3. Run the full suite: `python -W ignore -m unittest discover -p "test_*.py"`.
4. `git diff --check`.
5. Commit on that branch.
6. `git push origin <branch>` (no force).
7. Check CI for the exact SHA.
8. Request an independent review.

No PR or merge without separate authorization.

### P2: read-only verifier, genesis and preflight

| Field | Content |
|---|---|
| Design refs | Section 2 (status axes, H5); 3.5 M1–M10 and E1–E7; 3.3.6 V1–V6 (read-only parts); 5.1.3.1 EDG library (read-only computation); 5.1.3.2 genesis, start modes, R-GEN-1a…e |
| Potential files | New read-only verifier package (e.g. `replay/state_verifier.py`), a canonical CJ / EDG / `psh` library shared with P3; `runtime/service.py` (remove the creation branch, require a verified startup context); a new startup module (steps 1–4 before any `Store` open); new `runtime/genesis_prepare.py`; `runtime/cloud.py` / `runtime/demo_runner.py` (read-only, non-creating preflights); `runtime/cloud_runner.py` (call order); test fixtures migrated away from implicit account creation |
| Dependencies | P2a (verifier and EDG library) needs nothing. P2b (genesis and startup) is a constructor change; it is best done after P3 / P4a stabilize the economic-write wrapper (fixture churn). Policy: DEC-8.18 (verifier), DEC-8.20 (approved). Authorization: DEC-8.22-c, DEC-8.22-i, DEC-8.22-f (preflight parts) |
| Tests | NM1–NM21, NH-style read-only checks; I-G12…I-G14, I-G17…I-G22; NG37–NG41, NG47–NG61; NC5–NC12 (A2 preflight checks) |
| Risks | Broad test-fixture impact (implicit account creation is relied on); a preflight that creates a DB (today `Store(path)` does) |
| Acceptance | The verifier classifies Owner-provided copies only and fails closed; normal entry points cannot create accounts; configuration and identity verified before any DB open; full suite and CI green; independent review |
| Rollback | Revert on the branch; the verifier is read-only and has no runtime effect |
| Gate | DEC-8.18 approved; DEC-8.22-c / i / f authorized; P2b after P3 / P4a (recommended order) |

### P3: REX, EDG, anchors and risk evidence

| Field | Content |
|---|---|
| Design refs | 5.1 (stages ST0–ST10, `psh`, REX layers, 5.1.2.1–5.1.2.3 F1 / F1b / A1–A4 / V-P1…V-P3, 5.1.2.2 fill-gate inputs); 5.1.3 (alternative B) and 5.1.3.1–5.1.3.3 (B-STRICT, EDG, genesis anchor, chain-head anchors); 5.1.4–5.1.6 (G14 / G15 oracle and replay) |
| Potential files | An **economic-write wrapper** around every `save_paper` call site (`runtime/service.py` guarded writes, `execution/position_catch_up.py` per bar, `execution/pending_order_gate.py`, submit) recording REX stage entries (alternative B: after commit, own transaction); the REX writer; the shared CJ / EDG / `psh` library (from P2a); the anchor tool (OP-6 support, read-only copy plus signing); an independent oracle and replayer (`replay/`, importing no runtime module) |
| Dependencies | P2a (canonical library). Policy: DEC-8.17 (approved), DEC-8.21b (**provisional**). Authorization: DEC-8.22-d |
| Tests | NG1–NG46, NG-B1…B4, I-G1…I-G16; observability-only proof (an REX failure never changes an economic row); determinism (I-H5-style) |
| Risks | REX size and cost; `psh` / EDG stability under encoder changes (I-H6); the oracle drifting from the runtime rules (item 23) |
| Acceptance | Every economic write covered; chain and coverage checks pass on test periods; the oracle reproduces F1, F1b, A1–A4, V1 sizing and the fill gate exactly; crash-between-commit-and-REX detected; independent review |
| Rollback | Revert on the branch (observability only; under alternative B no economic behaviour changes) |
| Gate | DEC-8.21b validated beyond PROVISIONAL, or explicitly authorized for implementation while provisional; DEC-8.22-d authorized |

### P4: R-HALT, A2 archive, integration and certification

| Field | Content |
|---|---|
| Design refs | 3.8 (Alternative 1 admission contract: `gate.admit`, `L(W)`, `read2` before `save_paper`, the post-halt persistence policy E / D / O / L / H / P / S, bookkeeping, I-R1a…I-R14); 3.11.1 (A2 archive, OAR-A, flat proof); section 5 G-8.INT; section 6 simulation |
| Potential files | P4a: the gate object and admit / `read2` at the P3 wrapper sites; persistence guards in `runtime/service.py`, `runtime/scheduler.py`, `runtime/demo_runner.py` close path; the signal handler in `runtime/cloud_runner.py`. P4b: the archive tool (backup API, OAR-A), `EXPERIMENT_FREEZE_P2.md` (on Owner instruction), the A2 preflight checks |
| Dependencies | P4a builds on the P3 wrapper. P4b needs P2b (genesis and startup) and P3 (EDG). Policy: DEC-8.17 / 8.17b (approved), DEC-8.20 (approved), DEC-8.19 (pending). Authorization: DEC-8.22-e / f; operational OP-2 / OP-3 / OP-6 / OP-7 later |
| Tests | NR1–NR31, POSIX race tests (CI), I-R1a…I-R14; NC1–NC12; then the section 6 simulation on Owner copies (OP-1) |
| Risks | Handler and thread constraints (CPython); persistence sites missed by the guards (I-R14 static check); operational feasibility of the archive and anchors |
| Acceptance | P4a: race tests and I-R13 hold; M-5 assessed by an independent review (it may close only then). P4b: archive and preflight tests pass; then G-8.INT measurement only after a separately authorized period |
| Rollback | Revert on the branch. Operational steps are reversible only by not performing them (no archive or genesis without OP authorization) |
| Gate | DEC-8.22-e / f authorized; P2b and P3 accepted; certification and activation separately authorized; Phase 8 certification only by the independent reviewer and the Owner |

## 5. Recommended sequence (adjusted to real repository dependencies)

The listed order (P1 → P2 → P3 → P4) is adjusted as follows:

1. **P1:** independent of everything else; smallest surface.
2. **P2a (read-only verifier plus the canonical CJ / EDG / `psh` library):** read-only, no runtime effect; provides the
   library that P3 must reuse, so digests are computed by one implementation.
3. **P3 (REX / EDG write side, plus the economic-write wrapper):** every `save_paper` call site is instrumented once.
4. **P4a (R-HALT):** admit and `read2` are placed in the **same wrapper** as P3. Doing it after P3 avoids touching every
   call site twice.
5. **P2b (sealed genesis, startup order, non-creating preflights):** this changes `OperationalRuntime` construction,
   which nearly every test fixture uses. Doing it after the wrapper work avoids a second large fixture migration. It
   must be complete before any new period exists.
6. **P4b (A2 archive, `X_P2` / `Y_P2`, integration, then certification steps):** last; requires P2b and P3, and
   operational authorizations.

## 6. Prompt for P1 (prepared; **do not execute** until its gate is met)

```text
AI TRADING FLOOR V2 — P8.7-P1 IMPLEMENTATION: LOW-1 HARDENING + TECHNICAL PER-SYMBOL CATCH-UP

PRECONDITIONS (STOP and report if any is not met; do not start):
- Owner approvals recorded: DEC-8.21 (LOW-1), DEC-8.13, DEC-8.14 (with explicit acceptance of STRICT blocking and
  provider unknowns; busy ≤ 500 ms, K = 4, subject to tests), DEC-8.15 (global conservative revision gate, §1.6),
  DEC-8.16 (mixed-path TECHNICAL_ONLY).
- Implementation authorized: DEC-8.22-a (LOW-1) and DEC-8.22-b (per-symbol catch-up, technical only).
- V2_PHASE8_P86_DESIGN.md committed; its commit SHA given as the design baseline.

SCOPE (only):
A. LOW-1 (design section 4): validate review rows (source, event_type, decision ∈ DECISIONS, review_key == anomaly_id
   of a recorded EVIDENCE_REVISION, non-blank reviewer, final consistent, previous_decision = literal decision of
   the immediately preceding row for the same anomaly). Invalid latest row → BLOCKED (INVALID_REVIEW_RECORD), no
   fallback. Historical invalid rows listed. REVIEW_WITHOUT_RECORD and CLASSIFICATION_MISMATCH blockers.
   History is never rewritten. File: runtime/revision_review.py + tests (section 4.6).
B. Per-symbol catch-up, TECHNICAL ONLY (design 1.1–1.7):
   - AI_FLOOR_V2_POSITION_CATCH_UP_SYMBOLS strict grammar (1.1); ValueError cases C2/C3/C6–C9; canonical order.
   - RuntimeConfig.v2_position_catch_up_symbols with __post_init__ invariants; catch_up_applies(symbol).
   - Fingerprint: OFF byte-identical; ON adds the canonical scope.
   - runtime/service.py: per-symbol branch at the three flag sites; out-of-scope symbols observe-only (1.5:
     exception isolation, short busy timeout, no shared state, EVIDENCE_OBSERVE_FAILED / _SKIPPED_BUSY,
     DEGRADED after K=4), legacy path byte-identical for decisions and economics.
   - Revision gate scope (1.6): all anomalies; out-of-scope non-material, managed_by NEWEST_BAR;
     ANOMALY_FOR_DISABLED_SYMBOL blocks.
   - Preflight (cloud and local): catch_up_scope_valid; catch_up_evidence_contiguous in STRICT mode (1.7); OFF
     report byte-identical.
   - replay/activation_preview.py: --catch-up-symbols replaces --first-scope; path CATCH_UP | NEWEST_BAR.
   - Certification eligibility: any scope other than all enabled symbols → TECHNICAL_ONLY (1.9.3).

FORBIDDEN:
- No change to storage/database.py (hash-pinned), the V1 strategy, Risk Engine, AI, the economic rules, prompts or
  providers.
- No activation, flag change in any environment, Render change, deploy, or operational DB access.
- No REX, R-HALT, genesis or A2 work (later packages).
- No PR or merge.
- Commits only on the new branch v2/phase8-p1 created from the design-baseline SHA, after the full suite passes and
  git diff --check is clean.

TESTS: design section 4.6; matrix B (C1–C9, F1, R1–R10, P1, V1, I1, S1); NE1–NE7; 1.5 isolation tests; the inventory
tests unchanged and passing; the full suite and CI green.

DELIVERABLE: per change, root reference to the design section; test evidence; full suite counts; CI run id; final SHA;
remaining risks; request for an independent review. Do not certify your own work. Stop at the end.

PAPER ONLY · REAL EXECUTION DISABLED · NAS100 OFF · PHASE 8 BLOCKED · HIGH-8.1 OPEN · M-5 OPEN · NO PHASE 9.
```

## 7. Blockers and pending decisions

| ID | Blocker or pending item | Effect |
|---|---|---|
| B-1 | `V2_PHASE8_P86_DESIGN.md` and this handoff are **uncommitted** (no commit authorization) | Implementation prompts cannot cite a design SHA; the Owner must authorize a documentation-only commit first |
| B-2 | The current Libro Maestro version is not accessible or verifiable (latest found v1.7 predates Phase 6–8) | **Deferred by the Owner (2026-10-08)**; this register is to be transferred later |
| B-3 | DEC-8.18 and 8.19 PENDING (DEC-8.13 / 8.14 / 8.15 / 8.16 / 8.21 approved 2026-10-08) | The P1 policy gate is met; the P2a verifier gate (DEC-8.18) is not |
| B-4 | DEC-8.22-a…j and OP-1…OP-7 NOT AUTHORIZED | No package may start |
| B-5 | DEC-8.21b PROVISIONAL | P3 needs validation, or an explicit authorization to implement while provisional |
| B-6 | The experiment's current state is unknown (DEC-8.18 procedure not run) | No A2 operational step can be planned in time |
| — | M-5 OPEN, HIGH-8.1 OPEN, Phase 8 BLOCKED | Unchanged |

PAPER ONLY · REAL EXECUTION DISABLED · NAS100 OFF · PHASE 8 BLOCKED · HIGH-8.1 OPEN · M-5 OPEN · NO PHASE 9.
