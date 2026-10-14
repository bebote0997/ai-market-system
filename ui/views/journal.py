def render(st, vm, durable_rows=()):
    st.header("JOURNAL")
    st.caption("In-memory snapshot only · durable persistence deferred")
    c = st.columns(5)
    symbol = c[0].selectbox("Symbol", ["ALL", "XAUUSD", "NAS100", "EURUSD"])
    run_id = c[1].text_input("Run ID")
    source = c[2].text_input("Agent / source")
    event = c[3].text_input("Event")
    date = c[4].date_input("Date", value=None)
    rows = []
    for item in (vm.journal if vm.sample else durable_rows):
        row = {"timestamp": str(item.timestamp), "run_id": item.run_id, "symbol": item.symbol,
               "source": item.entity_id, "event": item.event_type, "details": str(item.details)} if vm.sample else {
               "timestamp": item["timestamp"], "run_id": item["run_id"], "symbol": item["symbol"],
               "source": item["source"], "event": item["event_type"], "details": item["payload"]}
        if symbol != "ALL" and row["symbol"] != symbol:
            continue
        if run_id and run_id not in row["run_id"]:
            continue
        if source and source.lower() not in row["source"].lower():
            continue
        if event and event.lower() not in row["event"].lower():
            continue
        if date and str(date) not in row["timestamp"]:
            continue
        rows.append(row)
    st.dataframe(rows, use_container_width=True, hide_index=True) if rows else st.info("NO_DATA — no journal events in this snapshot.")
