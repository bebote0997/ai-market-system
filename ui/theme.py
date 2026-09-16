"""Visual tokens used by the observation console."""
TOKENS = {
    "background": "#0b1118", "surface-1": "#111b25", "surface-2": "#172431",
    "surface-3": "#1d2d3b", "border": "#304152", "text-primary": "#e8eef4",
    "text-secondary": "#9aabba", "info": "#67a8d9", "success": "#55b891",
    "caution": "#d8ae64", "danger": "#dc7777", "inactive": "#667887",
}


def apply(st):
    st.markdown("""<style>
    .stApp {background:#0b1118;color:#e8eef4}
    h1,h2,h3 {letter-spacing:.02em}
    div[data-testid="stMetric"] {background:#172431;border:1px solid #304152;border-radius:7px;padding:10px}
    .floor-card {background:#111b25;border:1px solid #304152;border-radius:7px;padding:12px;margin:8px 0}
    .safety {background:#172431;border-left:4px solid #d8ae64;padding:9px 13px;border-radius:6px;font-weight:700}
    .critical {background:#492625;border:1px solid #dc7777;padding:12px;border-radius:7px;font-weight:700}
    code,.mono {font-variant-numeric:tabular-nums}
    @media(max-width:1100px){.floor-card{padding:8px}}
    </style>""", unsafe_allow_html=True)
