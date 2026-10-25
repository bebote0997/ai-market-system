from ui.i18n_es import COLUMNS, NO_DATA, column, enabled_caption, status_text, tr
from ui.tables import dataframe
from ui.json_views import ordered

# OANDA demo mode: the PAPER account's numbers are not shown; cycle values before the epoch are not "current".
PAPER_HEALTH_KEYS = frozenset({"equity", "realized_pnl", "unrealized_pnl", "open_positions", "paper_broker"})
CYCLE_KEYS = ("last_run", "last_success", "last_run_id", "last_cycle_status")


def health_rows(operational_health, since=None):
    """(label, readable value) rows of ``runtime.health.health``; ``since`` = demo epoch (None in PAPER mode)."""
    from ui.views.dashboard import FIRST_CYCLE_WAIT, is_current
    from ui.views.luxury import display
    health = dict(operational_health)
    if since is not None:
        health = {k: v for k, v in health.items() if k not in PAPER_HEALTH_KEYS}
        if not is_current(health.get("last_run"), since):
            health.update({key: FIRST_CYCLE_WAIT for key in CYCLE_KEYS if key in health})
        elif not is_current(health.get("last_success"), since):
            health["last_success"] = FIRST_CYCLE_WAIT
    rows = []
    for key, value in health.items():
        if isinstance(value, (tuple, list)):
            value = ", ".join(map(str, value))
        elif isinstance(value, str):
            value = display(value) if value[:2] == "20" else status_text(value)
        rows.append((column(key) if key in COLUMNS else key.replace("_", " ").capitalize(), value))
    return rows


def render(st, vm, operational_health=None, review=None, notifications=(), evidence_json=None,
           store=None, since=None):
    st.header(tr("SYSTEM"))
    st.caption(tr("Read-only service inventory"))
    S, E = column("service"), column("status")
    rows = [
        {S: "Símbolos soportados", E: "XAUUSD, NAS100, EURUSD"},
        {S: "Símbolos activos", E: enabled_caption(("XAUUSD", "EURUSD"))},
        {S: "Proveedor de precios", E: "Twelve Data configurado para la práctica interna"},
        {S: "Adaptador Massive", E: "Soportado / no activo"},
        {S: "Proveedor de IA", E: "No iniciado"},
        {S: "Programador", E: "No configurado"},
        {S: "Broker de práctica", E: "No iniciado"},
        {S: "Persistencia", E: "No configurada"},
        {S: "Último ciclo exitoso", E: NO_DATA},
        {S: "Actualidad de datos", E: status_text(vm.freshness)},
    ]
    if operational_health and not vm.sample:
        rows = [{S: label, E: value} for label, value in health_rows(operational_health, since)]
    dataframe(st, rows, hide_index=True, width="stretch")
    st.write("Avisos:", vm.warnings or "—")
    st.write("Versiones de prompts:", dict(vm.prompt_versions) if vm.prompt_versions else NO_DATA)
    st.write("Versión del esquema:", vm.schema_version or NO_DATA)
    if operational_health and not vm.sample:
        st.subheader(tr("Latest review"))
        if review:
            st.json(ordered({"run_id": review["run_id"], "final_status": review["final_status"],
                     "setup_status": review["setup_status"], "agents": review["agents"],
                     "risk_decision": review["risk_decision"], "execution": review.get("execution"),
                     **({} if since is not None else {"paper": review["paper"]}),
                     "error": review["error"]}), expanded=False)
        else:
            st.write(NO_DATA)
        if store is not None:
            st.subheader(tr("Audit by run ID"))
            run_id = st.text_input(tr("Run ID"), key="system_audit_run_id").strip()
            if run_id:
                from runtime.review_export import run_evidence_json
                record = store.review_report(run_id)
                if record is None:
                    st.info(tr("Run ID not found"))
                else:
                    st.json(ordered({key: record.get(key) for key in ("run_id", "symbol", "as_of",
                            "final_status", "setup_status", "risk_decision", "execution",
                            "paper", "error")}), expanded=False)
                    dataframe(st, store.journal(run_id=run_id, limit=500),
                                 hide_index=True, width="stretch")
                    st.download_button(tr("Download run evidence (JSON)"),
                                       run_evidence_json(store, run_id),
                                       file_name="paper-run-evidence.json", mime="application/json")
        st.subheader(tr("Notifications"))
        dataframe(st, [{column("timestamp_utc"): e["timestamp_utc"], column("type"): e["type"],
                       column("severity"): e["severity"], column("run_id"): e["run_id"]}
                      for e in notifications[-20:]], hide_index=True, width="stretch")
        if evidence_json is not None:
            st.download_button(tr("Download review evidence (JSON)"), evidence_json,
                               file_name="paper-review-evidence.json", mime="application/json")
