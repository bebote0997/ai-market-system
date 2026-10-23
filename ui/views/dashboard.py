"""Read-only Command Center views of published evidence, never runtime actions."""
from ui.tables import dataframe
import json
import os
import sqlite3
from itertools import groupby
from pathlib import Path

from ui.adapters import from_persisted_snapshot
from ui.components import panels
from ui.state import dashboard_database
from ui import i18n_es as es
from ui.i18n_es import tr

PAGES = ("OVERVIEW", "POSITIONS", "SETUPS", "DECISIONS", "HEALTH", "ALERTS", "HISTORY", "ERRORS")
TABLES = frozenset({"runs", "setups", "agent_decisions", "risk_decisions",
                   "paper_accounts", "paper_orders", "paper_fills", "paper_positions", "closed_trades",
                   "review_reports", "journal", "system_state", "ui_snapshots",
                   "notification_events", "notification_deliveries", "component_health", "alert_deliveries"})


ROW_LIMIT = 200
# DEC-OANDA-ONLY-1: PAPER ledger / P&L tables hidden from these pages when AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO.
PAPER_LEDGER_TABLES = frozenset({"paper_accounts", "paper_orders", "paper_fills", "paper_positions", "closed_trades"})


# PAPER fills / opens / closes and the PAPER daily summary are PAPER results too (journal side of the ledger).
PAPER_RESULT_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED", "ORDER_REJECTED",
                                 "ORDER_CANCELLED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED", "DAILY_SUMMARY"})
# P&L / fill / exit keys stripped from every other displayed row (any depth).
PAPER_RESULT_KEYS = frozenset({"net_pnl", "gross_pnl", "pnl", "realized_pnl", "unrealized_pnl", "total_pnl",
                               "today_pnl", "equity", "starting_equity", "balance", "fill_price", "exit_price",
                               "costs", "r_multiple", "realized_r"})


def paper_hidden():
    try:
        from execution.results_source import oanda_only
        return oanda_only(os.environ)
    except Exception:  # noqa: BLE001 - unreadable flag: current behaviour
        return False


def scrub_paper_results(value):
    if isinstance(value, dict):
        return {k: scrub_paper_results(v) for k, v in value.items() if k not in PAPER_RESULT_KEYS}
    if isinstance(value, list):
        return [scrub_paper_results(item) for item in value]
    if isinstance(value, str) and value[:1] == "{" and any(f'"{key}"' in value for key in PAPER_RESULT_KEYS):
        try:
            return json.dumps(scrub_paper_results(json.loads(value)), sort_keys=True)
        except ValueError:
            return value
    return value


def hide_paper_results(rows):
    """DEC-OANDA-ONLY-1 presentation filter over ``history()``/``records()`` rows: drops PAPER ledger rows and PAPER
    result journal events, strips P&L / fill / exit keys from the rest. The persisted evidence is untouched."""
    return [scrub_paper_results(row) for row in rows
            if row.get("record") not in PAPER_LEDGER_TABLES and row.get("event_type") not in PAPER_RESULT_EVENTS]


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
        st.info(es.NO_DATA)
        return
    dataframe(st, rows, hide_index=True, width="stretch")


def render(st, page):
    st.header(es.LEGACY_PAGES.get(page, page))
    st.caption(tr("Persisted PAPER evidence · SQLite read-only · missing evidence = NO_DATA"))
    hidden = paper_hidden()
    if hidden:
        from execution.results_source import PAPER_HIDDEN_NOTE
        st.info(PAPER_HIDDEN_NOTE)
    try:
        with dashboard_database() as db:
            if page == "OVERVIEW":
                accounts = [] if hidden else payloads(db, "paper_accounts")
                if not accounts and not hidden:
                    st.info(es.NO_DATA)
                for account in accounts:
                    st.subheader(account["account_id"])
                    for col, key in zip(st.columns(3), ("equity", "realized_pnl", "unrealized_pnl")):
                        col.metric(es.column(key), account.get(key, es.NO_DATA))
                st.subheader(tr("Market state / charts"))
                snapshots = payloads(db, "ui_snapshots")
                for snapshot in snapshots:
                    vm = from_persisted_snapshot(snapshot)
                    st.write(vm.symbol, es.status_text(vm.state), es.status_text(vm.freshness))
                    panels.chart(st, vm)
                if not snapshots:
                    st.info(es.NO_DATA)
            elif page == "POSITIONS" and hidden:
                pass  # PAPER positions / orders are internal in OANDA-demo-only mode
            elif page == "POSITIONS":
                st.subheader(tr("Open positions · all symbols"))
                table(st, payloads(db, "paper_positions", statuses=("OPEN",)))
                st.subheader(tr("Pending orders · all symbols"))
                table(st, payloads(db, "paper_orders", statuses=("PENDING",)))
            elif page == "SETUPS":
                st.subheader(tr("Latest setup per symbol"))
                runs = {row["slot_key"]: row for row in records(db, "runs")}
                latest = {}
                for row in records(db, "setups"):
                    run = runs.get(row["slot_key"])
                    if run and (run["symbol"] not in latest or run["as_of"] > latest[run["symbol"]]["as_of"]):
                        latest[run["symbol"]] = {**row, "run_id": run["run_id"],
                                                   "symbol": run["symbol"], "as_of": run["as_of"]}
                table(st, list(latest.values()))
            elif page == "DECISIONS":
                st.subheader(tr("AI decisions · interpretation"))
                table(st, records(db, "agent_decisions"))
                st.subheader(tr("Risk decisions · deterministic authority"))
                table(st, records(db, "risk_decisions"))
            elif page == "HEALTH":
                st.subheader(tr("System Health observations"))
                trading_path = Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db"))
                health_path = os.environ.get("AI_FLOOR_HEALTH_DB_PATH", str(trading_path.with_name("system_health.db")))
                with dashboard_database(health_path) as health_db:
                    table(st, payloads(health_db, "component_health"))
                st.caption(tr("Persisted component observations; no probes or health policy changes are performed."))
                states = {row["key"]: row["value"] for row in records(db, "system_state")}
                table(st, [{es.column("service"): key, es.column("status"): states.get(key, es.NO_DATA)} for key in
                           ("scheduler", "heartbeat", "scheduler_slot", "market_data_provider",
                            "market_provider_mode", "ai_provider", "macro_provider", "api_status",
                            "last_run", "last_success", "phase7_readiness")])
                st.caption(tr("Provider configuration is not proof of API health. Statuses are persisted observations."))
                st.subheader(tr("Provider / API run evidence"))
                table(st, [{key: row.get(key) for key in
                            ("run_id", "symbol", "as_of", "status", "provider_metadata", "error")}
                           for row in records(db, "runs")[:20]])
            elif page == "ALERTS":
                st.subheader(tr("Phase 9 alert deliveries"))
                ledger_path = os.environ.get("AI_FLOOR_ALERTS_LEDGER_PATH")
                if ledger_path:
                    with dashboard_database(ledger_path) as ledger_db:
                        table(st, records(ledger_db, "alert_deliveries"))
                else:
                    st.info(es.NO_DATA)
                st.subheader(tr("V1 notification deliveries"))
                events = {row["event_id"]: json.loads(row["payload"])
                          for row in records(db, "notification_events")}
                rows = [{**events.get(row["event_id"], {}), **row}
                        for row in records(db, "notification_deliveries")]
                table(st, sorted(rows, key=lambda row: row["attempted_at"], reverse=True)[:50])
            elif page == "HISTORY":
                run_id = st.text_input(tr("Run ID"), key="history_run_id").strip()
                setup_id = st.text_input(tr("Setup ID"), key="history_setup_id").strip()
                page_number = st.number_input(tr("History page"), min_value=1, value=1, step=1,
                                              key="history_page")
                st.caption(tr("Exact persisted IDs; both fields combine with AND. Up to 200 rows per table/page, newest first."))
                rows = history(db, run_id, setup_id, offset=(page_number - 1) * ROW_LIMIT)
                if hidden:
                    rows = hide_paper_results(rows)
                if not rows:
                    st.info(es.NO_DATA)
                for record_type, group in groupby(rows, key=lambda row: row["record"]):
                    st.subheader(record_type)
                    table(st, list(group))
            elif page == "ERRORS":
                st.subheader(tr("Failed runs"))
                table(st, records(db, "runs", failed=True))
                st.subheader(tr("Error journal"))
                errors = records(db, "journal", statuses=("ERROR", "CRITICAL"))
                table(st, hide_paper_results(errors) if hidden else errors)
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        st.error(f"{tr('DASHBOARD_ERROR')} ({type(exc).__name__}).")
