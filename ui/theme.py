"""Visual tokens used by the observation console (navy Floor theme, same palette as ui/static/theme.css)."""
TOKENS = {
    "background": "#05071a", "surface-1": "#0a1028", "surface-2": "#070b1e",
    "surface-3": "#121b1d", "border": "#1a2744", "text-primary": "#e8eceb",
    "text-secondary": "#a9b8b6", "info": "#22d3ee", "success": "#3ddc84",
    "caution": "#f0b450", "danger": "#ff4d6d", "inactive": "#7f9593", "gold": "#22d3ee",
}


def apply(st):
    st.markdown("""<style>
    .stApp {background:#05071a;color:#e8eceb}
    div[data-testid="stMetric"] {background:#0a1028;border:1px solid #1a2744;border-radius:14px;padding:12px}
    .floor-card {background:#0a1028;border:1px solid #1a2744;border-radius:14px;padding:12px;margin:8px 0}
    .safety {background:#0a1028;color:#22d3ee;border:1px solid #1e4a7a;padding:9px 13px;border-radius:12px;font-weight:700}
    .critical {background:#2a0f16;color:#ff8da1;border:1px solid #5c1f2c;padding:12px;border-radius:12px;font-weight:700}
    code,.mono {font-variant-numeric:tabular-nums}
    @media(max-width:1100px){.floor-card{padding:8px}}
    @media(max-width:640px){
      div[data-testid="stHorizontalBlock"]{flex-wrap:wrap}
      div[data-testid="stColumn"]{min-width:100%!important;flex:1 1 100%!important}
      .block-container{padding:1rem}
    }
    </style>""", unsafe_allow_html=True)
