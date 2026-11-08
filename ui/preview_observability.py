"""Isolated Streamlit view of the observability screens. Example rows only.

Run: ``streamlit run ui/preview_observability.py --server.address=127.0.0.1``.
No dashboard password, no emergency button, no provider button, no broker call.
Importing this module renders nothing and writes nothing.
"""
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path("/tmp/obs-preview-fixture")
TRADING = ROOT / "trading.db"
BROKER = ROOT / "broker.db"
HEALTH = ROOT / "health.db"


def _example_files():
    """Create the example databases once. They live under /tmp and are not the trading account."""
    ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["AI_FLOOR_DB_PATH"] = str(TRADING)
    os.environ["AI_FLOOR_BROKER_STATE_DB_PATH"] = str(BROKER)
    os.environ["AI_FLOOR_HEALTH_DB_PATH"] = str(HEALTH)
    os.environ["AI_FLOOR_AI_PROVIDER"] = "xai"
    os.environ["AI_FLOOR_BROKER_DEMO_ROUTING"] = "0"
    os.environ.pop("AI_FLOOR_DASHBOARD_PASSWORD", None)
    if TRADING.exists():
        return
    from dataclasses import replace
    from runtime.config import RuntimeConfig
    from runtime.provider_transition import record_provider_transition
    from storage.database import Store
    origin = replace(RuntimeConfig(db_path=TRADING, ai_provider_mode="openai"), scheduler_enabled=True)
    dest = replace(origin, ai_provider_mode="xai")
    with closing(Store(TRADING)) as store:
        record_provider_transition(
            store.db, from_payload=origin.fingerprint_content(), to_payload=dest.fingerprint_content(),
            flat_counts={"paper_open": 0, "paper_pending": 0, "broker_live": 0},
            operator_note="ejemplo", git_commit="a" * 40, starting_equity=10000.0, actor="ejemplo",
            recorded_at=datetime(2026, 11, 8, 0, 32, tzinfo=timezone.utc))
        store.db.execute(
            "INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status,final_status,provider_metadata) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("eur-1", "run-eur-1", "EURUSD", "2026-11-06T21:45:00+00:00", "2026-11-06T21:45:00+00:00",
             "COMPLETED", "COMPLETED", '{"provider":"openai"}'))
        store.db.execute(
            "INSERT INTO setups(slot_key,status,side) VALUES(?,?,?)",
            ("eur-1", "WATCH", "LONG"))
        store.db.execute(
            "INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
            "VALUES(?,?,?,?,?,?,?)",
            ("2026-11-06T21:45:00+00:00", "run-eur-1", "EURUSD", "ejemplo", "CYCLE_NOTE", "INFO",
             '{"note":"fila de ejemplo"}'))
        store.set_state("heartbeat", "2026-11-08T00:39:00+00:00")
        store.set_state("last_run", "2026-11-06T21:45:00+00:00")
        store.set_state("scheduler", "RUNNING")
        store.set_state("ai_provider", "ERROR")
        store.set_state("api_status", "NO_DATA")
        store.set_state("macro_provider", "NOT_CHECKED")
        store.db.commit()
    with sqlite3.connect(BROKER) as con:
        con.execute("CREATE TABLE broker_demo_flags (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL)")
        con.execute("INSERT INTO broker_demo_flags VALUES (?,?,?)",
                    ("provider_transition_fence", "1", "2026-11-08T00:32:00+00:00"))
        con.execute("INSERT INTO broker_demo_flags VALUES (?,?,?)",
                    ("provider_transition_request", "{}", "2026-11-08T00:31:00+00:00"))
        con.execute("INSERT INTO broker_demo_flags VALUES (?,?,?)",
                    ("provider_transition_receipt", '{"state":"pending","reason":"requested"}',
                     "2026-11-08T00:31:00+00:00"))


def main():
    import streamlit as st
    from ui.nav_menu import chrome, install
    from ui.views import dashboard, luxury
    from ui.views.control import provider_observation
    _example_files()
    st.set_page_config(page_title="Ejemplo · observabilidad", layout="wide")
    luxury.apply(st)
    st.markdown("**DATOS DE EJEMPLO.** No es la cuenta en producción. "
                "No hay contraseña, ni parada, ni botón que escriba.")
    with st.sidebar:
        luxury.html(st, chrome(None))
        pages = ("Salud", "Historial", "Control")
        wanted = st.query_params.get("vista", "Salud")
        if wanted not in pages:
            wanted = "Salud"
        page = st.radio("Vista", pages, index=pages.index(wanted), key="preview-page")
        install(st)
    if page == "Salud":
        dashboard.render(st, "HEALTH")
    elif page == "Historial":
        dashboard.render(st, "HISTORY")
    else:
        for line in provider_observation(broker_path=BROKER):
            st.write(line)


if __name__ == "__main__":
    main()
