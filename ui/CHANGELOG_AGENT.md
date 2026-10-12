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
