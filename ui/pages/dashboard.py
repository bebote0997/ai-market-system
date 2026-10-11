"""Read-only Command Center views of published evidence, never runtime actions."""
import json
import os
import sqlite3
from pathlib import Path

from ui.adapters import from_persisted_snapshot
from ui.components import panels
from ui.state import dashboard_database

PAGES = ("OVERVIEW", "POSITIONS", "SETUPS", "DECISIONS", "HEALTH", "ALERTS", "HISTORY", "ERRORS")
TABLES = frozenset({"runs", "setups", "agent_decisions", "risk_decisions",
                   "paper_accounts", "paper_orders", "paper_positions", "closed_trades",
                   "review_reports", "journal", "system_state", "ui_snapshots",
                   "notification_events", "notification_deliveries", "component_health"})


def records(db, table):
    if table not in TABLES:
        raise ValueError("unsupported dashboard table")
    if db is None or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')]


def payloads(db, table):
    return [{**row, **json.loads(row["payload"])} for row in records(db, table)]


def identities(value, key):
    """Only explicit identity fields count; prose and geometry never create IDs."""
    if isinstance(value, dict):
        result = {str(value[key])} if value.get(key) is not None else set()
        for item in value.values():
            result.update(identities(item, key))
        return result
    if isinstance(value, list):
        return set().union(*(identities(item, key) for item in value))
    return set()


def history(db, run_id="", setup_id=""):
    runs = records(db, "runs")
    by_slot = {row["slot_key"]: row for row in runs}
    rows = [{"record": "runs", **row} for row in runs]
    for table in ("setups", "review_reports", "paper_orders", "paper_positions", "closed_trades", "journal"):
        for original in records(db, table):
            row = dict(original)
            for key in ("payload", "plan", "evidence"):
                if row.get(key):
                    row[key] = json.loads(row[key])
            if isinstance(row.get("payload"), dict):
                for key in ("run_id", "setup_id", "symbol"):
                    if key not in row and key in row["payload"]:
                        row[key] = row["payload"][key]
            parent = by_slot.get(row.get("slot_key"), {})
            rows.append({"record": table, "run_id": parent.get("run_id"),
                         "symbol": parent.get("symbol"), **row})
    return [row for row in rows
            if (not run_id or run_id in identities(row, "run_id"))
            and (not setup_id or setup_id in identities(row, "setup_id"))]


def table(st, rows):
    if not rows:
        st.info("NO_DATA")
        return
    # Nested evidence stays visible as JSON without mixed Arrow column types.
    display = [{key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()} for row in rows]
    st.dataframe(display, hide_index=True, width="stretch")


def render(st, page):
    st.header(page)
    st.caption("Persisted PAPER evidence · SQLite read-only · missing evidence = NO_DATA")
    try:
        with dashboard_database() as db:
            if page == "OVERVIEW":
                accounts = payloads(db, "paper_accounts")
                if not accounts:
                    st.info("NO_DATA")
                for account in accounts:
                    st.subheader(account["account_id"])
                    for col, key in zip(st.columns(3), ("equity", "realized_pnl", "unrealized_pnl")):
                        col.metric(key.replace("_", " ").title(), account.get(key, "NO_DATA"))
                st.subheader("Market state / charts")
                snapshots = payloads(db, "ui_snapshots")
                for snapshot in snapshots:
                    vm = from_persisted_snapshot(snapshot)
                    st.write(vm.symbol, vm.state, vm.freshness)
                    panels.chart(st, vm)
                if not snapshots:
                    st.info("NO_DATA")
            elif page == "POSITIONS":
                st.subheader("Open positions · all symbols")
                table(st, [row for row in payloads(db, "paper_positions") if row["status"] == "OPEN"])
                st.subheader("Pending orders · all symbols")
                table(st, [row for row in payloads(db, "paper_orders") if row["status"] == "PENDING"])
            elif page == "SETUPS":
                st.subheader("Latest setup per symbol")
                runs = {row["slot_key"]: row for row in records(db, "runs")}
                latest = {}
                for row in records(db, "setups"):
                    run = runs.get(row["slot_key"])
                    if run and (run["symbol"] not in latest or run["as_of"] > latest[run["symbol"]]["as_of"]):
                        latest[run["symbol"]] = {**row, "run_id": run["run_id"],
                                                   "symbol": run["symbol"], "as_of": run["as_of"]}
                table(st, list(latest.values()))
            elif page == "DECISIONS":
                st.subheader("AI decisions · interpretation")
                table(st, records(db, "agent_decisions"))
                st.subheader("Risk decisions · deterministic authority")
                table(st, records(db, "risk_decisions"))
            elif page == "HEALTH":
                st.subheader("System Health observations")
                trading_path = Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db"))
                health_path = os.environ.get("AI_FLOOR_HEALTH_DB_PATH", str(trading_path.with_name("system_health.db")))
                with dashboard_database(health_path) as health_db:
                    table(st, payloads(health_db, "component_health"))
                st.caption("Persisted component observations; no probes or health policy changes are performed.")
                states = {row["key"]: row["value"] for row in records(db, "system_state")}
                table(st, [{"service": key, "status": states.get(key, "NO_DATA")} for key in
                           ("scheduler", "heartbeat", "scheduler_slot", "market_data_provider",
                            "market_provider_mode", "ai_provider", "macro_provider", "api_status",
                            "last_run", "last_success", "phase7_readiness")])
                st.caption("Provider configuration is not proof of API health. Statuses are persisted observations.")
                st.subheader("Provider / API run evidence")
                table(st, [{key: row.get(key) for key in
                            ("run_id", "symbol", "as_of", "status", "provider_metadata", "error")}
                           for row in records(db, "runs")[-20:]])
            elif page == "ALERTS":
                st.subheader("Recent notification deliveries")
                events = {row["event_id"]: json.loads(row["payload"])
                          for row in records(db, "notification_events")}
                rows = [{**events.get(row["event_id"], {}), **row}
                        for row in records(db, "notification_deliveries")]
                table(st, sorted(rows, key=lambda row: row["attempted_at"], reverse=True)[:50])
            elif page == "HISTORY":
                run_id = st.text_input("Run ID", key="history_run_id").strip()
                setup_id = st.text_input("Setup ID", key="history_setup_id").strip()
                st.caption("Exact persisted IDs; both fields combine with AND. Empty fields show all history.")
                table(st, history(db, run_id, setup_id))
            elif page == "ERRORS":
                st.subheader("Failed runs")
                table(st, [row for row in records(db, "runs") if row.get("error") or row["status"] == "FAILED"])
                st.subheader("Error journal")
                table(st, [row for row in records(db, "journal") if row["severity"] in {"ERROR", "CRITICAL"}])
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        st.error(f"DASHBOARD_ERROR — {type(exc).__name__}: persisted evidence could not be read")
