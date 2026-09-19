"""Remote Streamlit read gate; runtime state is never touched here."""
import hmac
import os


def remote_dashboard_enabled(env=None):
    env = os.environ if env is None else env
    return env.get("RENDER") == "true" or env.get("AI_FLOOR_REMOTE_DASHBOARD") == "1"


def require_dashboard_access(st, env=None):
    env = os.environ if env is None else env
    if not remote_dashboard_enabled(env):
        return True
    password = env.get("AI_FLOOR_DASHBOARD_PASSWORD")
    if not password:
        st.error("Dashboard access is not configured")
        st.stop()
        return False
    if st.session_state.get("dashboard_authenticated") is True:
        return True
    st.title("AI Trading Floor · private PAPER dashboard")
    supplied = st.text_input("Password", type="password")
    if st.button("Sign in") and hmac.compare_digest(supplied, password):
        st.session_state["dashboard_authenticated"] = True
        st.rerun()
    st.stop()
    return False
