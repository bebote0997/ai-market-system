"""Visual tokens used by the observation console (dark + gold Floor theme, same palette as ui/static/theme.css)."""
TOKENS = {
    "background": "#0a0a0b", "surface-1": "#0d1416", "surface-2": "#0d0d0e",
    "surface-3": "#121b1d", "border": "#1a2325", "text-primary": "#e8eceb",
    "text-secondary": "#a9b8b6", "info": "#19e3d3", "success": "#3ddc84",
    "caution": "#f0b450", "danger": "#ff4d6d", "inactive": "#7f9593", "gold": "#d9b45a",
}


def apply(st):
    st.markdown("""<style>
    .stApp {background:#0a0a0b;color:#e8eceb}
    div[data-testid="stMetric"] {background:#0d1416;border:1px solid #1a2325;border-radius:14px;padding:12px}
    .floor-card {background:#0d1416;border:1px solid #1a2325;border-radius:14px;padding:12px;margin:8px 0}
    .safety {background:#0d1416;color:#d9b45a;border:1px solid #3a3220;padding:9px 13px;border-radius:12px;font-weight:700}
    .critical {background:#2a0f16;color:#ff8da1;border:1px solid #5c1f2c;padding:12px;border-radius:12px;font-weight:700}
    code,.mono {font-variant-numeric:tabular-nums}
    @media(max-width:1100px){.floor-card{padding:8px}}
    @media(max-width:640px){
      div[data-testid="stHorizontalBlock"]{flex-wrap:wrap}
      div[data-testid="stColumn"]{min-width:100%!important;flex:1 1 100%!important}
      .block-container{padding:1rem}
    }
    </style>""", unsafe_allow_html=True)
