from ui.components import panels


def render(st, vm):
    st.header("POSITIONS")
    panels.positions(st, vm)
    panels.risk(st, vm)
