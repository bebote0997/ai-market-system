import json

from ui.i18n_es import COLUMNS, NO_DATA, column, enabled_caption, status_text, tr
from ui.tables import dataframe
from ui.i18n_es import readable, IDENTITY_KEYS

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
        if key in IDENTITY_KEYS:
            continue
        if isinstance(value, (tuple, list)):
            value = ", ".join(map(str, value))
        elif isinstance(value, str):
            value = display(value) if value[:2] == "20" else status_text(value)
        rows.append((column(key) if key in COLUMNS else key.replace("_", " ").capitalize(), value))
    return rows


def demo_health(health, store, notifications, since):
    """Read-only demo projection: historical PAPER freshness cannot degrade demo health."""
    from ui.views.dashboard import FIRST_CYCLE_WAIT, is_current, records, since_epoch
    if since is None:
        return health
    result = dict(health)
    errors = [e for e in since_epoch(notifications, since)
              if e.get("severity") in {"ERROR", "CRITICAL"}]
    if store is not None:
        errors += since_epoch(records(store.db, "journal", statuses=("ERROR", "CRITICAL")), since)
        errors += since_epoch(records(store.db, "runs", failed=True), since)
    current = is_current(result.get("last_run"), since)
    failed = current and result.get("last_cycle_status") in {"ERROR", "CRITICAL", "FAILED"}
    result["status"] = "DEGRADED" if errors or failed else (health.get("status", "OK") if current else "OK")
    if not current:
        for key in (*CYCLE_KEYS, "freshness", "phase7_readiness", "last_review_agents"):
            if key in result:
                result[key] = FIRST_CYCLE_WAIT
    if store is not None:
        from ui.views.dashboard import notification_rows, payloads
        result["notification_events"] = len(since_epoch(payloads(store.db, "notification_events"), since))
        result["slack_delivery_attempts"] = len(notification_rows(store.db, since))
    else:
        for key in ("notification_events", "slack_delivery_attempts"):
            result.pop(key, None)
    return result


def filter_evidence_json(evidence_json, since):
    """Filter the already sanitized export in the presentation layer using the dashboard epoch rule."""
    from ui.views.dashboard import is_current, since_epoch
    if evidence_json is None or since is None:
        return evidence_json
    bundle = json.loads(evidence_json)
    if "review" in bundle:
        if not is_current(bundle["review"].get("as_of"), since):
            return None
        bundle["journal"] = since_epoch(bundle.get("journal", []), since)
    else:
        bundle["reviews"] = since_epoch(bundle.get("reviews", []), since, ("as_of",))
        bundle["notification_events"] = since_epoch(bundle.get("notification_events", []), since,
                                                   ("timestamp_utc",))
    return json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))


def render(st, vm, operational_health=None, review=None, notifications=(), evidence_json=None,
           store=None, since=None):
    from ui.views.dashboard import demo_since, is_current, since_epoch
    since = demo_since() if since is None else since
    if review and not is_current(review.get("as_of"), since):
        review = None
    notifications = since_epoch(notifications, since, ("timestamp_utc",))
    if operational_health:
        operational_health = demo_health(operational_health, store, notifications, since)
    evidence_json = filter_evidence_json(evidence_json, since)
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
    st.write("Avisos:", readable(vm.warnings))
    st.write("Versiones de prompts:", readable(dict(vm.prompt_versions)))
    st.write("Versión del esquema:", vm.schema_version or NO_DATA)
    if operational_health and not vm.sample:
        st.subheader(tr("Latest review"))
        if review:
            st.write(readable({"run_id": review["run_id"], "final_status": review["final_status"],
                     "setup_status": review["setup_status"], "agents": review["agents"],
                     "risk_decision": review["risk_decision"], "execution": review.get("execution"),
                     **({} if since is not None else {"paper": review["paper"]}),
                     "error": review["error"]}))
        else:
            from ui.views.dashboard import FIRST_CYCLE_WAIT
            st.write(FIRST_CYCLE_WAIT if since is not None else NO_DATA)
        if store is not None:
            st.subheader(tr("Audit by run ID"))
            run_id = st.text_input(tr("Run ID"), key="system_audit_run_id").strip()
            if run_id:
                from runtime.review_export import run_evidence_json
                record = store.review_report(run_id)
                if record is None:
                    st.info(tr("Run ID not found"))
                elif not is_current(record.get("as_of"), since):
                    st.info("ese ciclo es anterior al inicio del demo y no se muestra")
                else:
                    st.write(readable({key: record.get(key) for key in ("run_id", "symbol", "as_of",
                            "final_status", "setup_status", "risk_decision", "execution",
                            "paper", "error")}))
                    dataframe(st, since_epoch(store.journal(run_id=run_id, limit=500), since),
                                 hide_index=True, width="stretch")
                    st.download_button(tr("Download run evidence (JSON)"),
                                       filter_evidence_json(run_evidence_json(store, run_id), since),
                                       file_name="paper-run-evidence.json", mime="application/json")
        st.subheader(tr("Notifications"))
        if not notifications and since is not None:
            from ui.views.dashboard import FIRST_CYCLE_WAIT
            st.info(FIRST_CYCLE_WAIT)
        dataframe(st, [{column("timestamp_utc"): e["timestamp_utc"], column("type"): e["type"],
                       column("severity"): e["severity"], column("run_id"): e["run_id"]}
                      for e in notifications[-20:]], hide_index=True, width="stretch")
        if evidence_json is not None:
            st.download_button(tr("Download review evidence (JSON)"), evidence_json,
                               file_name="paper-review-evidence.json", mime="application/json")
