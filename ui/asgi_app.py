"""ASGI entry point used by ``streamlit run ui/asgi_app.py`` (runtime.cloud): the same dashboard (``ui/app.py``) wrapped
with the security-headers middleware. Every ``--server.*`` / ``--client.*`` flag of ``streamlit run`` keeps working.
Local development can still use ``streamlit run ui/app.py`` (no headers)."""
import streamlit as st
from starlette.middleware import Middleware

from ui.security_headers import SecurityHeadersMiddleware

app = st.App("app.py", middleware=[Middleware(SecurityHeadersMiddleware)])
