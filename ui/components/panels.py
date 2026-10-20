"""Presentation only. All financial numbers come from view models/contracts."""
from datetime import datetime, timezone

from ui.adapters import assistant_lines, state_label
from ui.components.charts import price_action_figure
from ui.i18n_es import AGENTS, NO_DATA, column, status_text, tr


def show_value(value):
    return "—" if value is None else str(value)


def header(st, vm):
    st.title(tr("AI TRADING FLOOR"))
    st.markdown('<div class="safety">MODO PRÁCTICA (PAPER) &nbsp; · &nbsp; EJECUCIÓN REAL DESACTIVADA &nbsp; · &nbsp; '
                'SIN DINERO REAL</div>', unsafe_allow_html=True)
    if vm.sample:
        st.warning(tr("SAMPLE / DEMO — NOT CURRENT PRICES"))
    if vm.freshness == "STALE_DATA":
        st.markdown(f'<div class="critical">{tr("CRITICAL — STALE DATA")}</div>', unsafe_allow_html=True)
    c = st.columns(6)
    for col, label, value in zip(c, ("SÍMBOLO", "ESTADO", "ACTUALIDAD", "SESIÓN", "HORA UTC", "ÚLTIMO ANÁLISIS"),
            (vm.symbol, tr("CRITICAL — STALE DATA") if vm.freshness == "STALE_DATA" else status_text(state_label(vm.state)),
             status_text(vm.freshness), "—", datetime.now(timezone.utc).strftime("%H:%M"),
             vm.as_of.isoformat() if vm.as_of else "—")):
        col.metric(label, value)
    st.caption(tr("NEXT ANALYSIS: NOT SCHEDULED · AI INTERPRETS. PYTHON VALIDATES. RISK ENGINE AUTHORIZES. PAPER BROKER EXECUTES."))


def chart(st, vm):
    st.subheader(tr("PRICE ACTION · 15M STRUCTURE"))
    if vm.freshness == "STALE_DATA":
        st.error(tr("CRITICAL — STALE DATA · historical observation only"))
    if not vm.bars:
        st.info(tr("NO_DATA — no closed 15M bars in this snapshot."))
        return
    try:
        fig = price_action_figure(vm.bars, vm.annotations)
        st.plotly_chart(fig, width="stretch", theme=None, key=f"chart-{vm.symbol}")
    except Exception as exc:
        st.error(f"{tr('CHART_ERROR')} — {type(exc).__name__}")
    cols = st.columns(3)
    for col, label, status in zip(cols, ("1 H · CONTEXTO", "15 MIN · ESTRUCTURA", "5 MIN · MOMENTO"),
                                  ("NO_DATA", "SAMPLE / DEMO" if vm.sample else "BARS PROVIDED", "NO_DATA")):
        col.caption(f"{label} · {status_text(status)}")


ASSISTANT_TITLES = {"FACTS": "HECHOS", "AI INTERPRETATION": "INTERPRETACIÓN DE LA IA",
                    "WARNINGS / UNCERTAINTY": "AVISOS / INCERTIDUMBRE"}


def assistant(st, vm):
    st.subheader(tr("FLOOR ASSISTANT"))
    for title, lines in assistant_lines(vm).items():
        st.caption(ASSISTANT_TITLES.get(title, title))
        for line in lines:
            st.write(tr(line) if line in ("NO REAL MONEY", "None reported") else status_text(line))
    st.caption(tr("Observation only. No authorization or execution controls."))


def agents(st, vm):
    st.subheader(tr("AGENT CONSENSUS"))
    for a in vm.agents:
        with st.expander(f"{AGENTS.get(a.name, a.name)} · {status_text(a.status)} · {status_text(a.bias)}"):
            st.write("Confianza:", show_value(a.confidence))
            st.write("Recomendación:", show_value(a.recommendation))
            st.write("Evidencia:", ", ".join(a.evidence) or "—")
            st.write("Conflictos:", ", ".join(a.conflicts) or "—")
            st.write("Avisos:", ", ".join(a.warnings) or "—")
            st.write("Hora:", a.timestamp.isoformat() if a.timestamp else "—")


def setup(st, vm):
    st.subheader(tr("SETUP"))
    st.write("Estado:", tr("CRITICAL — STALE DATA") if vm.freshness == "STALE_DATA" else
             status_text(state_label(vm.state)) if vm.state == "PLAN_READY" else status_text(vm.setup_status))
    st.write("Side:", status_text(vm.setup_side) if vm.setup_side else "—")
    for label, value in (("Entry", getattr(vm.plan, "entry", None)), ("Stop loss", getattr(vm.plan, "stop", None)),
                         ("Take profit (TP)", getattr(vm.plan, "target", None)), ("R:R", getattr(vm.plan, "risk_reward", None)),
                         ("Invalidation", vm.setup_invalidation)):
        st.write(f"{label}:", show_value(value))
    st.caption("Evidencia: " + (", ".join(map(str, vm.setup_evidence)) or "—"))


def risk(st, vm):
    st.subheader(tr("DETERMINISTIC RISK ENGINE"))
    st.caption(tr("PYTHON DETERMINISTIC"))
    st.write("Decisión:", status_text(vm.risk_status))
    d = vm.risk_decision
    for label, value in (("Max risk per trade", "≤ 1%"), (column("risk_fraction"), getattr(d, "risk_fraction", None)),
                         (column("equity_at_decision"), getattr(d, "equity_at_decision", None)),
                         (column("capital_at_risk"), getattr(d, "capital_at_risk", None)),
                         ("Position size", getattr(d, "quantity", None)),
                         (column("reason"), getattr(d, "reason", None))):
        st.write(f"{label}:", show_value(value))


def positions(st, vm):
    st.subheader(tr("PAPER POSITION"))
    st.caption(tr("NO REAL MONEY"))
    if not vm.positions and not vm.orders:
        st.write(tr("FLAT"))
    for order in vm.orders:
        st.write(f"{order.symbol} · {status_text(order.status)} · planned entry {show_value(order.planned_entry)} · "
                 f"size {show_value(order.quantity)}")
    for position in vm.positions:
        st.write(f"{position.symbol} · {status_text(position.status)} · fill {show_value(position.entry_price)} · "
                 f"SL {show_value(position.stop)} · TP {show_value(position.target)}")
    if vm.account is not None:
        st.write("Unrealized PnL:", show_value(vm.account.unrealized_pnl))
        st.write("Realized PnL:", show_value(vm.account.realized_pnl))


def pipeline(st, vm):
    st.subheader(tr("PIPELINE"))
    ai_states = [a.status for a in vm.agents]
    stages = [("Datos de mercado", "DONE" if vm.bars else "NO_DATA"),
              ("Estructura ∥ Liquidez ∥ Macro", "DONE" if all(s == "OK" for s in ai_states[:3]) else "NO_DATA"),
              ("Revisión IA", "DONE" if ai_states[3] == "OK" else "SKIPPED"),
              ("Validador de oportunidad (Python)", "DONE" if vm.setup_status != "NO_SETUP" else "WAITING"),
              ("Planificador", "DONE" if vm.plan else "WAITING"),
              ("Revisión IA de la operación", "DONE" if ai_states[4] == "OK" else "SKIPPED"),
              ("Motor de riesgo", vm.risk_status if vm.risk_status != "NOT CALLED" else "WAITING"),
              ("Broker de práctica", "SKIPPED"), ("Gestor de operaciones", "SKIPPED")]
    st.caption("PASO ACTUAL: " + next((name for name, status in stages if status in {"WAITING", "NO_DATA"}),
                                      status_text("COMPLETE")))
    st.write(" → ".join(f"{name} [{status_text(status)}]" for name, status in stages))


def meeting(st, vm):
    st.subheader(tr("AI MEETING"))
    st.caption(f"ID DEL CICLO: {show_value(vm.run_id)} · estado final: {status_text(vm.setup_status)} · "
               f"motor de riesgo: {status_text(vm.risk_status)}")
    for a in vm.agents:
        with st.expander(f"{AGENTS.get(a.name, a.name)} · {status_text(a.status)}"):
            st.write("Acuerdo / recomendación:", show_value(a.recommendation))
            st.write("Observaciones:", a.observations or "—")
            st.write("Evidencia:", a.evidence or "—")
            st.write("Desacuerdos / conflictos:", a.conflicts or "—")
            st.write("Avisos:", a.warnings or "—")
            st.write("Resumen del razonamiento:", show_value(a.reasoning_summary) if a.reasoning_summary else NO_DATA)
