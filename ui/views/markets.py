from ui.adapters import MARKETS, symbol_enablement
from ui.components import panels


def render(st, vm, market_lookup, enabled_symbols):
    st.header("MARKETS")
    rows = []
    for symbol in MARKETS:
        item = market_lookup(symbol)
        rows.append({"symbol": symbol, "experiment": symbol_enablement(symbol, enabled_symbols),
                     "analysis_state": item.state,
                     "AI contextual bias": next((a.bias for a in item.agents if a.status == "OK"), "UNKNOWN"),
                     "risk_state": item.risk_status, "freshness": item.freshness})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    panels.chart(st, vm)
    st.caption("1H context · 15M structure · 5M timing. Bias is context, not an order.")
