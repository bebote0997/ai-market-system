def render(st, vm, operational_health=None, review=None, notifications=(), evidence_json=None,
           store=None):
    st.header("SYSTEM")
    st.caption("Read-only service inventory")
    rows = [
        {"service": "Supported symbols", "status": "XAUUSD, NAS100, EURUSD"},
        {"service": "Enabled symbols", "status": "XAUUSD, EURUSD"},
        {"service": "Market data provider", "status": "Twelve Data configured for PAPER"},
        {"service": "Massive adapter", "status": "SUPPORTED / NOT ACTIVE"},
        {"service": "AI provider", "status": "NOT STARTED"},
        {"service": "Scheduler", "status": "NOT CONFIGURED"},
        {"service": "Paper broker", "status": "NOT STARTED"},
        {"service": "Persistence", "status": "NOT CONFIGURED"},
        {"service": "Last successful run", "status": "NO_DATA"},
        {"service": "Freshness", "status": vm.freshness},
    ]
    if operational_health and not vm.sample:
        rows = [{"service": key.replace("_", " ").title(), "status": value} for key, value in operational_health.items()]
    st.dataframe(rows, hide_index=True, use_container_width=True)
    st.write("Warnings:", vm.warnings or "—")
    st.write("Prompt versions:", dict(vm.prompt_versions) if vm.prompt_versions else "NO_DATA")
    st.write("Schema version:", vm.schema_version or "NO_DATA")
    if operational_health and not vm.sample:
        st.subheader("Latest review")
        if review:
            st.json({"run_id": review["run_id"], "final_status": review["final_status"],
                     "setup_status": review["setup_status"], "agents": review["agents"],
                     "risk_decision": review["risk_decision"], "execution": review.get("execution"),
                     "paper": review["paper"],
                     "error": review["error"]})
        else:
            st.write("NO_DATA")
        if store is not None:
            st.subheader("Audit by run ID")
            run_id = st.text_input("Run ID", key="system_audit_run_id").strip()
            if run_id:
                from runtime.review_export import run_evidence_json
                record = store.review_report(run_id)
                if record is None:
                    st.info("Run ID not found")
                else:
                    st.json({key: record.get(key) for key in ("run_id", "symbol", "as_of",
                            "final_status", "setup_status", "risk_decision", "execution",
                            "paper", "error")})
                    st.dataframe(store.journal(run_id=run_id, limit=500),
                                 hide_index=True, use_container_width=True)
                    st.download_button("Download run evidence (JSON)",
                                       run_evidence_json(store, run_id),
                                       file_name="paper-run-evidence.json", mime="application/json")
        st.subheader("Notifications")
        st.dataframe([{"timestamp_utc": e["timestamp_utc"], "type": e["type"],
                       "severity": e["severity"], "run_id": e["run_id"]}
                      for e in notifications[-20:]], hide_index=True, use_container_width=True)
        if evidence_json is not None:
            st.download_button("Download review evidence (JSON)", evidence_json,
                               file_name="paper-review-evidence.json", mime="application/json")
