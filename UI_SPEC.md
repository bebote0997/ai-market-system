# AI Trading Floor UI — Fase 6B

## Launch

Python 3.13: `python -m pip install -r requirements-ui.txt`, then `streamlit run ui/app.py` from the repository root. The legacy `streamlit run app.py` remains separate. No API key, provider, scheduler, database, broker, or network call is required to start the floor.

## Architecture

`ui/app.py` is the Streamlit entry point and page composer. `ui/theme.py` holds design tokens. `ui/state.py` reads an explicit in-memory snapshot registry; a Streamlit rerun never calls `floor.orchestrator.run`, `ai.orchestrator.run`, a provider, or Paper Broker. `ui/adapters.py` is a pure adapter from completed `AIFloorReport` to immutable `FloorViewModel`. `ui/components/` renders charts and panels. `ui/pages/` contains FLOOR, MARKETS, POSITIONS, JOURNAL, EXPERIMENT, and SYSTEM. AI MEETING is an expander linked to the selected `run_id`. `ui/fixtures/demo_floor.py` contains isolated fictional sample bars and report data.

The integration boundary is `FloorRunReport → AIFloorReport → from_ai_report(...) → FloorViewModel → renderer`. The caller must publish a completed view model through `ui.state.set_snapshot`; no automatic analysis occurs on page load. `RiskDecision is None` maps to `NOT CALLED`, and only `RiskDecision.status` supplies approval or rejection. `PLAN_READY` requires an actual approved RiskDecision or maps to `STATE_INCONSISTENCY`. AI bias is context only.

## Display and safety

The header always displays `PAPER MODE`, `REAL EXECUTION DISABLED`, symbol, state, freshness, and last analysis. The risk card is visually separate and labeled `PYTHON DETERMINISTIC`. The paper position card states `NO REAL MONEY`. Missing values render as `—`, `NO_DATA`, or `MISSING`; absent confidence and prices are never synthesized. Assistant output groups facts, AI interpretation, and warnings. Macro facts have a separate view-model field from AI interpretation. Sample mode is explicitly labeled `SAMPLE / DEMO — NOT CURRENT PRICES`; its bars are fictional and cannot enter the backend.

`STALE_DATA` produces `CRITICAL — STALE DATA`, with the floor status retained for audit. Provider errors remain visible as `PROVIDER_FAILURE`. Partial or missing AI yields `PARTIAL_AI_FAILURE`. Charts are made only from supplied bars, with source-labeled annotations. Potential levels are dashed. No indicator, order button, live broker, scheduler, or persistence is present.

## Responsive and Streamlit limits

The page uses wide layout and native columns; Streamlit stacks columns on narrow displays. CSS styles only the app background and owned markup, not private Streamlit class names. At compact widths the global safety header remains above every page. Streamlit cannot provide exact breakpoint-dependent column restructuring without a custom component, so the layout is adaptive rather than pixel-locked at 1600/1440/1100. The pipeline is a concise text preview; no custom JavaScript synchronization is used. Charts use `st.plotly_chart(..., use_container_width=True, theme=None)` supported by installed Streamlit 1.64.0. No fragments, cache, or rerun calls are used.

Experiment and System show explicit `NOT STARTED`, `NOT CONFIGURED`, or `NO_DATA` for unconnected services. Journal filters operate only on in-memory events supplied with a snapshot. Durable persistence and scheduler are Phase 6C work.
