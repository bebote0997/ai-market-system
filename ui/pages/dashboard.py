"""Read-only Command Center views of published evidence, never runtime actions."""
from ui.tables import dataframe
import json
import os
import sqlite3
from pathlib import Path

from ui.adapters import from_persisted_snapshot
from ui.components import panels
from ui.state import dashboard_database

PAGES = ("OVERVIEW", "POSITIONS", "SETUPS", "DECISIONS", "HEALTH", "ALERTS", "HISTORY", "ERRORS")
TABLES = frozenset({"runs", "setups", "agent_decisions", "risk_decisions",
                   "paper_accounts", "paper_orders", "paper_fills", "paper_positions", "closed_trades",
                   "review_reports", "journal", "system_state", "ui_snapshots",
                   "notification_events", "notification_deliveries", "component_health", "alert_deliveries"})


ROW_LIMIT = 200


def records(db, table, *, run_id="", setup_id="", offset=0, statuses=(), failed=False):
    """Filter in SQL before returning a bounded page, newest published rows first."""
    if table not in TABLES:
        raise ValueError("unsupported dashboard table")
    if db is None or not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    predicates, params = [], []
    for key, value in (("run_id", run_id), ("setup_id", setup_id)):
        if not value:
            continue
        matches = []
        if key in columns:
            matches.append(f'CAST(t."{key}" AS TEXT) = ?')
            params.append(value)
        for column in ("payload", "plan", "evidence"):
            if column in columns:
                matches.append(f'EXISTS (SELECT 1 FROM json_tree(t."{column}") '
                               'WHERE key = ? AND CAST(atom AS TEXT) = ?)')
                params.extend((key, value))
        if key == "run_id" and "slot_key" in columns and table != "runs":
            matches.append('EXISTS (SELECT 1 FROM runs r WHERE r.slot_key=t.slot_key AND r.run_id=?)')
            params.append(value)
        predicates.append("(" + " OR ".join(matches or ["0"]) + ")")
    if statuses:
        key = "severity" if table == "journal" else "status"
        field = f't."{key}"' if key in columns else f"json_extract(t.payload, '$.{key}')"
        status_filter = field + " IN (" + ",".join("?" for _ in statuses) + ")"
        params.extend(statuses)
        if table == 'setups' and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_snapshots'").fetchone():
            # Preserve exact-cycle snapshot validation on legacy WATCH rows and
            # their persisted plan/IDs while filtering eligible setups before LIMIT.
            status_filter = '(' + status_filter + ' OR EXISTS (SELECT 1 FROM ui_snapshots s JOIN runs r '
            status_filter += 'ON r.slot_key=t.slot_key WHERE s.symbol=r.symbol '
            status_filter += "AND json_extract(s.payload,'$.symbol')=r.symbol AND json_extract(s.payload,'$.run_id')=r.run_id "
            status_filter += "AND COALESCE(json_extract(s.payload,'$.setup_status'),json_extract(s.payload,'$.state')) IN ("
            status_filter += ','.join('?' for _ in statuses) + ')))'
            params.extend(statuses)
        predicates.append(status_filter)
    if failed:
        predicates.append("(t.status='FAILED' OR COALESCE(t.error, '') <> '')")
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    timestamp = next((name for name in ("attempted_at", "claimed_at", "as_of", "timestamp")
                      if name in columns), None)
    # Identity/payload tables use deterministic publication order.
    order = f't."{timestamp}" DESC, t.rowid DESC' if timestamp else 't.rowid DESC'
    return [dict(row) for row in db.execute(
        f'SELECT t.* FROM "{table}" t{where} ORDER BY {order} LIMIT ? OFFSET ?',
        (*params, ROW_LIMIT, offset))]


def payloads(db, table, **filters):
    return [{**row, **json.loads(row["payload"])} for row in records(db, table, **filters)]


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


def history(db, run_id="", setup_id="", offset=0):
    runs = records(db, "runs", run_id=run_id, setup_id=setup_id, offset=offset)
    by_slot = {row["slot_key"]: row for row in runs}
    rows = [{"record": "runs", **row} for row in runs]
    for table in ("setups", "review_reports", "paper_orders", "paper_fills", "paper_positions", "closed_trades", "journal"):
        for original in records(db, table, run_id=run_id, setup_id=setup_id, offset=offset):
            row = dict(original)
            for key in ("payload", "plan", "evidence"):
                if row.get(key):
                    row[key] = json.loads(row[key])
            if isinstance(row.get("payload"), dict):
                for key in ("run_id", "setup_id", "symbol"):
                    if key not in row and key in row["payload"]:
                        row[key] = row["payload"][key]
            parent = by_slot.get(row.get("slot_key"), {})
            if not parent and row.get("slot_key"):
                persisted = db.execute("SELECT run_id,symbol FROM runs WHERE slot_key=?",
                                       (row["slot_key"],)).fetchone()
                parent = dict(persisted) if persisted else {}
            rows.append({"record": table, "run_id": parent.get("run_id"),
                         "symbol": parent.get("symbol"), **row})
    return [row for row in rows
            if (not run_id or run_id in identities(row, "run_id"))
            and (not setup_id or setup_id in identities(row, "setup_id"))]


def table(st, rows):
    if not rows:
        st.info("NO_DATA")
        return
    dataframe(st, rows, hide_index=True, width="stretch")


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
                table(st, payloads(db, "paper_positions", statuses=("OPEN",)))
                st.subheader("Pending orders · all symbols")
                table(st, payloads(db, "paper_orders", statuses=("PENDING",)))
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
                           for row in records(db, "runs")[:20]])
            elif page == "ALERTS":
                st.subheader("Phase 9 alert deliveries")
                ledger_path = os.environ.get("AI_FLOOR_ALERTS_LEDGER_PATH")
                if ledger_path:
                    with dashboard_database(ledger_path) as ledger_db:
                        table(st, records(ledger_db, "alert_deliveries"))
                else:
                    st.info("NO_DATA")
                st.subheader("V1 notification deliveries")
                events = {row["event_id"]: json.loads(row["payload"])
                          for row in records(db, "notification_events")}
                rows = [{**events.get(row["event_id"], {}), **row}
                        for row in records(db, "notification_deliveries")]
                table(st, sorted(rows, key=lambda row: row["attempted_at"], reverse=True)[:50])
            elif page == "HISTORY":
                run_id = st.text_input("Run ID", key="history_run_id").strip()
                setup_id = st.text_input("Setup ID", key="history_setup_id").strip()
                page_number = st.number_input("History page", min_value=1, value=1, step=1,
                                              key="history_page")
                st.caption("Exact persisted IDs; both fields combine with AND. Up to 200 rows per table/page, newest first.")
                table(st, history(db, run_id, setup_id, offset=(page_number - 1) * ROW_LIMIT))
            elif page == "ERRORS":
                st.subheader("Failed runs")
                table(st, records(db, "runs", failed=True))
                st.subheader("Error journal")
                table(st, records(db, "journal", statuses=("ERROR", "CRITICAL")))
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        st.error(f"DASHBOARD_ERROR — {type(exc).__name__}: persisted evidence could not be read")
