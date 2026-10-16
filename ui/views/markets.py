from ui.i18n_es import column, status_text, tr
from ui.tables import dataframe
from ui.adapters import MARKETS, symbol_enablement
from ui.components import panels


def render(st, vm, market_lookup, enabled_symbols):
    st.header(tr("MARKETS"))
    rows = []
    for symbol in MARKETS:
        item = market_lookup(symbol)
        rows.append({column("symbol"): symbol, column("experiment"): status_text(symbol_enablement(symbol, enabled_symbols)),
                     column("analysis_state"): status_text(item.state),
                     column("AI contextual bias"): status_text(next((a.bias for a in item.agents if a.status == "OK"), "UNKNOWN")),
                     column("risk_state"): status_text(item.risk_status), column("freshness"): status_text(item.freshness)})
    dataframe(st, rows, width="stretch", hide_index=True)
    panels.chart(st, vm)
    st.caption(tr("1H context · 15M structure · 5M timing. Bias is context, not an order."))
