from ui.i18n_es import column, status_text, tr
from ui.tables import dataframe


def render(st, vm, durable_rows=()):
    """``durable_rows``: persisted journal rows (demo mode: already filtered to the epoch by the caller)."""
    from ui.views.luxury import display
    st.header(tr("JOURNAL"))
    st.caption(tr("In-memory snapshot only · durable persistence deferred") if vm.sample else
               "Eventos guardados del sistema · horas de Nueva York (ET)")
    c = st.columns(5)
    symbol = c[0].selectbox("Símbolo", ["ALL", "XAUUSD", "NAS100", "EURUSD"], format_func=lambda v: "Todos" if v == "ALL" else v)
    run_id = c[1].text_input(tr("Run ID"))
    source = c[2].text_input("Agente / origen")
    event = c[3].text_input("Evento")
    date = c[4].date_input("Fecha", value=None)
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
    shown = [{**row, "timestamp": display(row["timestamp"]), "event": status_text(row["event"])} for row in rows]
    dataframe(st, [{column(k): v for k, v in row.items()} for row in shown], width="stretch", hide_index=True) if rows else st.info(tr("NO_DATA — no journal events in this snapshot."))
