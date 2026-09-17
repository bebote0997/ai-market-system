def render(st, vm, operational_health=None, review=None, notifications=()):
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
                     "risk_decision": review["risk_decision"], "paper": review["paper"],
                     "error": review["error"]})
        else:
            st.write("NO_DATA")
        st.subheader("Notifications")
        st.dataframe([{"timestamp_utc": e["timestamp_utc"], "type": e["type"],
                       "severity": e["severity"], "run_id": e["run_id"]}
                      for e in notifications[-20:]], hide_index=True, use_container_width=True)
