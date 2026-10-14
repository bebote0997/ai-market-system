# Phase 6B audit — Hybrid Trading Floor

- tests_before: 381 OK
- tests_after: 389 OK
- smoke_test: Streamlit 1.64.0 launched headless; `/_stcore/health` returned `ok`. No visual QA claim.
- docs: Context7 checked Streamlit `st.plotly_chart`/columns/tabs and Plotly `go.Candlestick`/annotations; Figma not required because no design file was supplied.
- modules: `ui/adapters.py`, `ui/state.py`, `ui/theme.py`, `ui/components/`, `ui/pages/`, `ui/fixtures/`, `ui/app.py`
- integration: deterministic `FloorRunReport` → `ai.orchestrator.run` → `AIFloorReport` → `from_ai_report` → `FloorViewModel`, tested without mutation
- sample isolation: fixed fictional prices and `SAMPLE / DEMO` label, no runtime/provider calls
- risk authority: `RiskDecision` alone supplies `APPROVED`/`REJECTED`; absent decision is `NOT CALLED`
- read-only UI: page renderers consume view models and never invoke analysis or execution
- paper-only: header and position labels permanent; real execution disabled; no live order action
- debt deferred to 6C: durable journal, scheduler, provider health/telemetry, experiment metrics, live snapshot ingestion
