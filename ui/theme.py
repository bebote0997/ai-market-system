"""Visual tokens used by the observation console (light theme)."""
TOKENS = {
    "background": "#f3f6fb", "surface-1": "#ffffff", "surface-2": "#f7f9fc",
    "surface-3": "#eef2f8", "border": "#e2e8f0", "text-primary": "#172033",
    "text-secondary": "#556070", "info": "#2459d6", "success": "#16a34a",
    "caution": "#b54708", "danger": "#dc2626", "inactive": "#98a2b3",
}


def apply(st):
    st.markdown("""<style>
    .stApp {background:#f3f6fb;color:#172033}
    div[data-testid="stMetric"] {background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:12px}
    .floor-card {background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:12px;margin:8px 0}
    .safety {background:#e9f0fe;color:#2459d6;padding:9px 13px;border-radius:12px;font-weight:700}
    .critical {background:#fdecea;color:#b42318;border:1px solid #f5c6c0;padding:12px;border-radius:12px;font-weight:700}
    code,.mono {font-variant-numeric:tabular-nums}
    @media(max-width:1100px){.floor-card{padding:8px}}
    @media(max-width:640px){
      div[data-testid="stHorizontalBlock"]{flex-wrap:wrap}
      div[data-testid="stColumn"]{min-width:100%!important;flex:1 1 100%!important}
      .block-container{padding:1rem}
    }
    </style>""", unsafe_allow_html=True)
