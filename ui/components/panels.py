"""Presentation only. All financial numbers come from view models/contracts."""
from datetime import datetime, timezone

from ui.adapters import assistant_lines, state_label
from ui.components.charts import price_action_figure


def show_value(value):
    return "—" if value is None else str(value)


def header(st, vm):
    st.title("AI TRADING FLOOR")
    st.markdown('<div class="safety">PAPER MODE &nbsp; · &nbsp; REAL EXECUTION DISABLED &nbsp; · &nbsp; NO REAL MONEY</div>', unsafe_allow_html=True)
    if vm.sample:
        st.warning("SAMPLE / DEMO — NOT CURRENT PRICES")
    if vm.freshness == "STALE_DATA":
        st.markdown('<div class="critical">CRITICAL — STALE DATA</div>', unsafe_allow_html=True)
    c = st.columns(6)
    for col, label, value in zip(c, ("SYMBOL", "STATE", "FRESHNESS", "SESSION", "UTC", "LAST ANALYSIS"),
            (vm.symbol, "CRITICAL — STALE DATA" if vm.freshness == "STALE_DATA" else state_label(vm.state), vm.freshness, "—", datetime.now(timezone.utc).strftime("%H:%M"),
             vm.as_of.isoformat() if vm.as_of else "—")):
        col.metric(label, value)
    st.caption("NEXT ANALYSIS: NOT SCHEDULED · AI INTERPRETS. PYTHON VALIDATES. RISK ENGINE AUTHORIZES. PAPER BROKER EXECUTES.")


def chart(st, vm):
    st.subheader("PRICE ACTION · 15M STRUCTURE")
    if vm.freshness == "STALE_DATA":
        st.error("CRITICAL — STALE DATA · historical observation only")
    if not vm.bars:
        st.info("NO_DATA — no closed 15M bars in this snapshot.")
        return
    try:
        fig = price_action_figure(vm.bars, vm.annotations)
        st.plotly_chart(fig, use_container_width=True, theme=None, key=f"chart-{vm.symbol}")
    except Exception as exc:
        st.error(f"CHART_ERROR — {type(exc).__name__}")
    cols = st.columns(3)
    for col, label, status in zip(cols, ("1H · CONTEXT", "15M · STRUCTURE", "5M · TIMING"),
                                  ("NO_DATA", "SAMPLE / DEMO" if vm.sample else "BARS PROVIDED", "NO_DATA")):
        col.caption(f"{label} · {status}")


def assistant(st, vm):
    st.subheader("FLOOR ASSISTANT")
    for title, lines in assistant_lines(vm).items():
        st.caption(title)
        for line in lines:
            st.write(line)
    st.caption("Observation only. No authorization or execution controls.")


def agents(st, vm):
    st.subheader("AGENT CONSENSUS")
    for a in vm.agents:
        with st.expander(f"{a.name} · {a.status} · {a.bias}"):
            st.write("Confidence:", show_value(a.confidence))
            st.write("Recommendation:", show_value(a.recommendation))
            st.write("Evidence:", ", ".join(a.evidence) or "—")
            st.write("Conflicts:", ", ".join(a.conflicts) or "—")
            st.write("Warnings:", ", ".join(a.warnings) or "—")
            st.write("Timestamp:", a.timestamp.isoformat() if a.timestamp else "—")


def setup(st, vm):
    st.subheader("SETUP")
    st.write("Status:", "CRITICAL — STALE DATA" if vm.freshness == "STALE_DATA" else state_label(vm.state) if vm.state == "PLAN_READY" else vm.setup_status)
    st.write("Side:", show_value(vm.setup_side))
    for label, value in (("Entry", getattr(vm.plan, "entry", None)), ("Stop", getattr(vm.plan, "stop", None)),
                         ("Target", getattr(vm.plan, "target", None)), ("R:R", getattr(vm.plan, "risk_reward", None)),
                         ("Invalidation", vm.setup_invalidation)):
        st.write(f"{label}:", show_value(value))
    st.caption("Evidence: " + (", ".join(map(str, vm.setup_evidence)) or "—"))


def risk(st, vm):
    st.subheader("DETERMINISTIC RISK ENGINE")
    st.caption("PYTHON DETERMINISTIC")
    st.write("Decision:", vm.risk_status)
    d = vm.risk_decision
    for label, value in (("Max risk", "≤ 1%"), ("Proposed risk", getattr(d, "risk_fraction", None)),
                         ("Equity", getattr(d, "equity_at_decision", None)),
                         ("Capital at risk", getattr(d, "capital_at_risk", None)),
                         ("Position size", getattr(d, "quantity", None)),
                         ("Reason", getattr(d, "reason", None))):
        st.write(f"{label}:", show_value(value))


def positions(st, vm):
    st.subheader("PAPER POSITION")
    st.caption("NO REAL MONEY")
    if not vm.positions and not vm.orders:
        st.write("FLAT")
    for order in vm.orders:
        st.write(f"{order.symbol} · {order.status} · planned {show_value(order.planned_entry)} · qty {show_value(order.quantity)}")
    for position in vm.positions:
        st.write(f"{position.symbol} · {position.status} · fill {show_value(position.entry_price)} · SL {show_value(position.stop)} · TP {show_value(position.target)}")
    if vm.account is not None:
        st.write("Unrealized PnL:", show_value(vm.account.unrealized_pnl))
        st.write("Realized PnL:", show_value(vm.account.realized_pnl))


def pipeline(st, vm):
    st.subheader("PIPELINE")
    ai_states = [a.status for a in vm.agents]
    stages = [("Market Data", "DONE" if vm.bars else "NO_DATA"),
              ("Structure ∥ Liquidity ∥ Macro", "DONE" if all(s == "OK" for s in ai_states[:3]) else "NO_DATA"),
              ("AI Review", "DONE" if ai_states[3] == "OK" else "SKIPPED"),
              ("Python Setup Validator", "DONE" if vm.setup_status != "NO_SETUP" else "WAITING"),
              ("Trade Planner", "DONE" if vm.plan else "WAITING"),
              ("AI Trade Review", "DONE" if ai_states[4] == "OK" else "SKIPPED"),
              ("Deterministic Risk Engine", vm.risk_status if vm.risk_status != "NOT CALLED" else "WAITING"),
              ("Paper Broker", "SKIPPED"), ("Trade Manager", "SKIPPED")]
    st.caption("CURRENT: " + next((name for name, status in stages if status in {"WAITING", "NO_DATA"}), "COMPLETE"))
    st.write(" → ".join(f"{name} [{status}]" for name, status in stages))


def meeting(st, vm):
    st.subheader("AI MEETING")
    st.caption(f"RUN ID: {show_value(vm.run_id)} · final deterministic state: {vm.setup_status} · Risk Engine: {vm.risk_status}")
    for a in vm.agents:
        with st.expander(f"{a.name} · {a.status}"):
            st.write("Agreement / recommendation:", show_value(a.recommendation))
            st.write("Observations:", a.observations or "—")
            st.write("Evidence references:", a.evidence or "—")
            st.write("Disagreement / conflicts:", a.conflicts or "—")
            st.write("Warnings:", a.warnings or "—")
            st.write("Reasoning summary:", show_value(a.reasoning_summary))
