# Owner-authorized dashboard work — 2026-10-11

## Process timeline

Auth precedes reads and fragment registration. Read-only per-cycle Process links run_id,
setup_id and slot_key with dashboard.history/records. Missing steps are labelled
`not persisted yet`; Gaps is calculated for each selected run. Auto-refresh is a
60-second fragment with sidebar pause. No runtime/configuration/database mutations.

Persistence audit (absence is conditional on selected run, never inferred as success):
- Data intake: provider metadata is in runs (`storage/database.py:162`); macro news
  is in review_reports (`runtime/review.py:65`). Neither means bars are retained.
- Cycle OHLC bars: runtime snapshot omits bars (`runtime/service.py:438`). Evidence
  Store retains versioned bars separately (`runtime/service.py:272`), but no exact
  per-run consumed-bar manifest exists in the trading rows. No timestamp-only join.
- Agents: agent_decisions (`storage/database.py:165`) plus exact-run snapshot fallback.
- Setup reviewer/deterministic setup: agent_decisions, setups and review.setup
  (`storage/database.py:175`, `runtime/review.py:74`). Legacy explanations may be absent.
- Conflict/opposite policy: journal opposite_policy events
  (`execution/opposite_policy.py:112`) and execution decision reasons
  (`storage/database.py:226`). No universal per-cycle conflict-engine report.
- Planner: setups.plan (`storage/database.py:173`), review.target_decision when present.
  Per-cycle planner policy is not stored in plan serialization; current env is not history.
- Risk: risk_decisions (`storage/database.py:178`); absent if not reached.
- Entry/exit: paper_orders, paper_positions, closed_trades payloads;
  absent when no order/fill/close happened. Absence is never interpreted as rejection.
- Outcome: runs status/final_status, linked closed-trade PnL; no invented cycle PnL.

Tests: baseline 1599 passed, 1 skipped, 1 xfailed; UI hashing covers empty/populated DBs.
Root project docs intentionally untouched to respect owner's HARD SCOPE.

## Setups charts and optional setup-chart email

Pure `ui/components/setup_chart.py:build_setup_figure` renders supplied 15m OHLC,
ET timestamps, persisted plan levels and risk/reward zones. Non-3R plans display
actual R:R, never an invented 3R target. Setups lists all validated records, with
exact-run snapshots only and explicit missing-bar/identity gaps.

`AI_FLOOR_SETUP_CHART_EMAIL=0` is the default. When explicitly 1, the existing
post-decision alert worker reconciles completed validated PAPER setups at startup,
on notification admission and every recovery interval. This covers V1 notification
suppression without modifying the journal or cycle. Uses the same alert ledger,
new channel `setup_chart_email`, dedup ID derived from persisted setup_id only,
and existing bounded retry/stale-claim recovery. Snapshot includes immutable plan
and bars for recovery. No new flag is added to RuntimeConfig or its fingerprint.
No flag activated, email sent to a real recipient, push, merge or deploy.

Gmail has optional backwards-compatible attachment arguments. Plotly/kaleido render
errors fall back to an email without an image, with explicit unavailable-chart text.
Missing bars also send without an image. Missing setup_id skips email rather than
inventing an identity. New dependencies: kaleido added to requirements.txt; existing
Plotly UI constraint mirrored there because the optional email worker needs it.
Kaleido is absent in this workspace; real PNG export is not exercised here. Tests
mock only export/SMTP and inspect the MIME attachment; missing-renderer fallback
is covered. A deployment needs Kaleido's browser renderer for real PNG export.

Exact files outside ui/ in this commit:
- requirements.txt
- runtime/alert_channels.py
- runtime/alert_dispatch.py
- runtime/alert_hook.py
- runtime/setup_chart_email.py
- test_dashboard_luxury.py (UI/chart/email integration tests)

Persistence gaps, exact names shown as applicable:
- Data intake: absent provider metadata/news/bars yields `not persisted yet`.
- Agent outputs: absent agent_decisions, exact-run snapshot and review agents.
- Setup reviewer + deterministic setup: absent setups/review.setup/setup journal.
- Conflict engine / opposite-setup policy: absent CONFLICT_DECISION or opposite
  policy journal. Events do exist in `execution/conflict_path.py:42` and
  `execution/opposite_policy.py:112`; no outcome is inferred when not recorded.
- Planner R:R and plan: absent setups.plan, target_decision and PLAN_CREATED.
- Risk Engine: absent risk_decisions and risk journal (`storage/database.py:178`).
- Paper Broker entry: absent linked orders/fills/positions; these may never occur.
- Paper Broker exit: absent closed_trades; an open/unexecuted setup has no exit yet.
- Final outcome: absent runs (empty database); no synthetic status or PnL.
- Cycle OHLC bars / Exact-cycle 15m OHLC bars: `_snapshot` omits bars
  (`runtime/service.py:438`); separate evidence store is not a consumed-run manifest.
- Macro provider/news evidence: review.macro_evidence empty or absent
  (`runtime/review.py:65`); metadata alone does not fabricate news.
- Per-cycle planner policy: not serialized in legacy plan fields
  (`storage/database.py:173`); current environment is not historical evidence.
- Step timestamp: a persisted row without a timestamp and no linked journal event
  is labelled absent. setups/risk_decisions have no timestamp columns
  (`storage/database.py:43`); matching journal timestamps are used when available.
- Setup ID not persisted: legacy setups.plan can omit setup_id
  (`storage/database.py:173`). Review.setup, review.execution and EXECUTION_DECISION
  provide an exact fallback where available; otherwise setup email is skipped.

Verification: first commit full suite 1601 passed, 1 skipped, 1 xfailed,
1150 subtests. Second commit checks include chart palette/geometry/ET, populated
and empty UI hashes, stale snapshot isolation, SMTP MIME, flag OFF, dedup/restart,
render fallback, and auth-first/refresh pause. Final full-suite result recorded
in the owner delivery summary.

Final second-commit validation: 1609 passed, 1 skipped, 1 xfailed, 1152 subtests
passed (owner's exact pytest command). Ruff F on ui and changed runtime/test files
PASS; git diff --check PASS. Pending immutable setup-event replay, OFF after prior
ON, absent-bar email fallback and bounded SMTP retry are also tested.

Local read-only audit: zero persisted runs available through dashboard_database.
Therefore the exact local missing-step list is all nine Process steps above;
the UI reports this explicitly. Populated offline SQLite fixtures exercise the
persisted-row paths; no sample/provider values are substituted in production UI.

## Glass UI — 2026-10-28 (`feat/glass-ui`)

Presentation only: glass layer appended to `ui/static/theme.css` (static ambient blobs, translucent blurred panels,
opaque fallbacks for no backdrop-filter / reduced transparency, no motion for reduced motion, AA contrast), phone sidebar
toggle made visible, Floor three-column layout with real-payload agent cards (`ui/components/glass.py`, `<details>`
closed on phones), manual close wrapped in `st.container(key="manual-close-card")` (logic unchanged), Inicio P&L
sparkline (real series only) and «Día N de 30» bar, Process step strip. Tests: `test_ui_glass.py`.
