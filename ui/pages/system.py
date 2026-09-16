def render(st, vm):
    st.header("SYSTEM")
    st.caption("Read-only service inventory")
    st.dataframe([
        {"service": "Market data provider", "status": "NOT CONFIGURED"},
        {"service": "AI provider", "status": "NOT STARTED"},
        {"service": "Scheduler", "status": "NOT CONFIGURED"},
        {"service": "Paper broker", "status": "NOT STARTED"},
        {"service": "Persistence", "status": "NOT CONFIGURED"},
        {"service": "Last successful run", "status": "NO_DATA"},
        {"service": "Freshness", "status": vm.freshness},
    ], hide_index=True, use_container_width=True)
    st.write("Warnings:", vm.warnings or "—")
    st.write("Prompt versions:", dict(vm.prompt_versions) if vm.prompt_versions else "NO_DATA")
    st.write("Schema version:", vm.schema_version or "NO_DATA")
