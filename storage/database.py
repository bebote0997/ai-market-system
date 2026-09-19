"""SQLite repository. Slot claims and paper+journal writes are transactional."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from storage.codec import paper_decode, paper_encode, public_metadata, safe_json, utc, parse_utc

SCHEMA_VERSION = 3
PHASE8_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_deliveries(
  event_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempted_at TEXT NOT NULL,
  completed_at TEXT, error_type TEXT,
  FOREIGN KEY(event_id) REFERENCES notification_events(event_id));
CREATE TABLE IF NOT EXISTS macro_awareness(
  event_id TEXT NOT NULL, symbol TEXT NOT NULL, first_run_id TEXT NOT NULL,
  PRIMARY KEY(event_id,symbol));
"""
PHASE7_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_reports(
  run_id TEXT PRIMARY KEY, slot_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
  FOREIGN KEY(slot_key) REFERENCES runs(slot_key));
CREATE TABLE IF NOT EXISTS notification_events(
  event_id TEXT PRIMARY KEY, journal_id INTEGER NOT NULL UNIQUE, payload TEXT NOT NULL,
  FOREIGN KEY(journal_id) REFERENCES journal(id));
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
  slot_key TEXT PRIMARY KEY, run_id TEXT, symbol TEXT NOT NULL, as_of TEXT NOT NULL,
  started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
  final_status TEXT, prompt_versions TEXT, provider_metadata TEXT, warnings TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS agent_decisions(
  slot_key TEXT NOT NULL, agent_name TEXT NOT NULL, as_of TEXT NOT NULL,
  status TEXT NOT NULL, bias TEXT, confidence REAL, recommendation TEXT,
  reasoning_summary TEXT, evidence TEXT, warnings TEXT, model_metadata TEXT,
  PRIMARY KEY(slot_key,agent_name), FOREIGN KEY(slot_key) REFERENCES runs(slot_key));
CREATE TABLE IF NOT EXISTS setups(slot_key TEXT PRIMARY KEY, status TEXT NOT NULL, side TEXT,
  invalidation TEXT, evidence TEXT, plan TEXT, FOREIGN KEY(slot_key) REFERENCES runs(slot_key));
CREATE TABLE IF NOT EXISTS risk_decisions(slot_key TEXT PRIMARY KEY, status TEXT NOT NULL,
  reason TEXT, equity REAL, risk_fraction REAL, quantity REAL, capital_at_risk REAL,
  entry REAL, stop REAL, target REAL, contract_multiplier REAL,
  FOREIGN KEY(slot_key) REFERENCES runs(slot_key));
CREATE TABLE IF NOT EXISTS paper_accounts(account_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_orders(order_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_fills(fill_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_positions(position_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS closed_trades(trade_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
  run_id TEXT, symbol TEXT, source TEXT NOT NULL,event_type TEXT NOT NULL,
  severity TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS system_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ui_snapshots(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_metadata(slot_key TEXT PRIMARY KEY,git_commit TEXT,
  config_fingerprint TEXT NOT NULL,experiment_id TEXT,starting_equity REAL NOT NULL,
  schema_version INTEGER NOT NULL, FOREIGN KEY(slot_key) REFERENCES runs(slot_key));
CREATE TABLE IF NOT EXISTS symbol_locks(symbol TEXT PRIMARY KEY,slot_key TEXT NOT NULL);
""" + PHASE7_SCHEMA + PHASE8_SCHEMA


class Store:
    def __init__(self, path, *, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode=ro" if readonly else str(self.path),
                                  uri=readonly, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        try:
            self._migrate()
        except BaseException:
            self.db.close()
            raise

    def _migrate(self):
        version_table = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_info'").fetchone()
        if version_table:
            rows = self.db.execute("SELECT version FROM schema_info").fetchall()
            if len(rows) != 1 or rows[0][0] not in {1, 2, SCHEMA_VERSION}:
                raise RuntimeError("incompatible database schema")
            required = {"runs", "agent_decisions", "setups", "risk_decisions", "paper_accounts",
                        "paper_orders", "paper_fills", "paper_positions", "closed_trades",
                        "journal", "system_state", "ui_snapshots", "run_metadata", "symbol_locks"}
            actual = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= actual:
                raise RuntimeError("incomplete database schema")
            if rows[0][0] == 1:
                if self.readonly:
                    raise RuntimeError("database schema upgrade required")
                self.db.executescript("BEGIN IMMEDIATE;" + PHASE7_SCHEMA +
                                      "UPDATE schema_info SET version=2;COMMIT;")
            if rows[0][0] <= 2:
                if self.readonly:
                    raise RuntimeError("database schema upgrade required")
                self.db.executescript("BEGIN IMMEDIATE;" + PHASE8_SCHEMA +
                                      f"UPDATE schema_info SET version={SCHEMA_VERSION};COMMIT;")
            if rows[0][0] >= 2:
                if not {"review_reports", "notification_events"} <= actual:
                    raise RuntimeError("incomplete database schema")
            if rows[0][0] == SCHEMA_VERSION and not {"notification_deliveries", "macro_awareness"} <= actual:
                raise RuntimeError("incomplete database schema")
        elif self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
            raise RuntimeError("unversioned database schema")
        else:
            if self.readonly:
                raise RuntimeError("database schema missing")
            self.db.executescript("BEGIN IMMEDIATE;" + SCHEMA +
                f"INSERT INTO schema_info(version) VALUES({SCHEMA_VERSION});COMMIT;")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def claim_slot(self, key, symbol, as_of, started_at, run_id=None):
        run_id = run_id or str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        with self.transaction():
            if self.db.execute("SELECT 1 FROM symbol_locks WHERE symbol=?", (symbol,)).fetchone():
                return False
            try:
                self.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES(?,?,?,?,?,?)",
                                (key, run_id, symbol, utc(as_of), utc(started_at), "RUNNING"))
            except sqlite3.IntegrityError:
                return False
            self.db.execute("INSERT INTO symbol_locks(symbol,slot_key) VALUES(?,?)", (symbol, key))
            self._event(started_at, run_id, symbol, "runtime", "RUN_STARTED", "INFO", {"slot_key": key})
            return True

    def owns_slot(self, key, symbol):
        row = self.db.execute("SELECT r.status,l.slot_key FROM runs r JOIN symbol_locks l ON l.symbol=r.symbol WHERE r.slot_key=? AND r.symbol=?", (key, symbol)).fetchone()
        return bool(row and row["status"] == "RUNNING" and row["slot_key"] == key)

    def _event(self, timestamp, run_id, symbol, source, event_type, severity="INFO", payload=None):
        self.db.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) VALUES(?,?,?,?,?,?,?)",
                        (utc(timestamp), run_id, symbol, source, event_type, severity, safe_json(payload or {})))

    def event(self, timestamp, run_id, symbol, source, event_type, severity="INFO", payload=None):
        with self.transaction():
            self._event(timestamp, run_id, symbol, source, event_type, severity, payload)

    def save_reports(self, key, deterministic, ai, audit_entries=()):
        from runtime.review import build_review
        setup = deterministic.setup_assessment
        plan = deterministic.trade_plan
        risk = deterministic.risk_decision
        with self.transaction():
            self.db.execute("UPDATE runs SET run_id=?,final_status=?,prompt_versions=?,provider_metadata=?,warnings=? WHERE slot_key=?",
                (ai.run_id, ai.final_status, safe_json(ai.prompt_versions), safe_json(public_metadata(ai.provider_metadata)), safe_json(ai.warnings), key))
            for response in (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review, ai.ai_trade_review):
                if response is None:
                    continue
                self.db.execute("""INSERT OR REPLACE INTO agent_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, response.agent_name, utc(response.as_of), response.status, response.bias,
                     response.confidence if response.status in {"OK", "PARTIAL"} else None,
                     response.recommendation, response.reasoning_summary,
                     safe_json({"supporting": response.supporting_evidence, "conflicting": response.conflicting_evidence}),
                     safe_json(response.warnings), safe_json(public_metadata(response.model_metadata))))
            plan_data = None if plan is None else {k: getattr(plan, k) for k in
                ("schema_version", "symbol", "side", "timeframe", "entry", "stop", "target", "risk_reward", "invalidation", "evidence", "cancel_conditions", "run_id", "as_of")}
            self.db.execute("INSERT OR REPLACE INTO setups VALUES(?,?,?,?,?,?)",
                (key, setup.status, setup.side, safe_json(setup.invalidation), safe_json(setup.evidence), safe_json(plan_data)))
            if risk is not None:
                self.db.execute("INSERT OR REPLACE INTO risk_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (key, risk.status, risk.reason, risk.equity_at_decision, risk.risk_fraction,
                     risk.quantity, risk.capital_at_risk, risk.entry, risk.stop, risk.target, risk.contract_multiplier))
            review = build_review(key, deterministic, ai, audit_entries)
            self.db.execute("INSERT INTO review_reports(run_id,slot_key,payload) VALUES(?,?,?)",
                            (ai.run_id, key, safe_json(review.payload())))

    def save_run_metadata(self, key, fingerprint, equity, git_commit=None, experiment_id=None):
        with self.transaction():
            self.db.execute("INSERT INTO run_metadata VALUES(?,?,?,?,?,?)",
                (key, git_commit, fingerprint, experiment_id, equity, SCHEMA_VERSION))

    def record_analysis_events(self, at, deterministic, ai):
        with self.transaction():
            for component, event_type, value in (
                ("structure", "STRUCTURE_COMPLETE", deterministic.structure_reports),
                ("liquidity", "LIQUIDITY_COMPLETE", deterministic.liquidity_reports),
                ("macro", "MACRO_COMPLETE", deterministic.macro_news_report)):
                self._event(at, ai.run_id, ai.symbol, component, event_type, "INFO",
                            {"status": "NO_DATA" if not value else "RECORDED"})
            self._event(at, ai.run_id, ai.symbol, "ai", "AI_REVIEW_COMPLETE", "INFO",
                        {"final_status": ai.final_status})
            setup = deterministic.setup_assessment
            self._event(at, ai.run_id, ai.symbol, "setup", "SETUP_" + setup.status, "INFO")
            if deterministic.trade_plan is not None:
                self._event(at, ai.run_id, ai.symbol, "planner", "PLAN_CREATED")
            if deterministic.risk_decision is not None:
                status = deterministic.risk_decision.status
                self._event(at, ai.run_id, ai.symbol, "risk", "RISK_" + status,
                            "INFO" if status == "APPROVED" else "WARNING")

    def finish(self, key, completed_at, status, final_status, error=None):
        with self.transaction():
            owner = self.db.execute("SELECT symbol FROM runs WHERE slot_key=? AND status='RUNNING'", (key,)).fetchone()
            if owner is None or not self.owns_slot(key, owner["symbol"]):
                return False
            self.db.execute("UPDATE runs SET completed_at=?,status=?,final_status=?,error=? WHERE slot_key=?",
                            (utc(completed_at), status, final_status, error, key))
            self.db.execute("DELETE FROM symbol_locks WHERE slot_key=?", (key,))
            row = self.db.execute("SELECT run_id,symbol,as_of FROM runs WHERE slot_key=?", (key,)).fetchone()
            existing_review = self.db.execute("SELECT payload FROM review_reports WHERE run_id=?", (row["run_id"],)).fetchone()
            if existing_review:
                review = json.loads(existing_review[0])
                review["final_status"] = final_status
                review["error"] = error
            else:
                review = {"schema_version": "1.0", "run_id": row["run_id"],
                          "slot_key": key, "symbol": row["symbol"], "as_of": row["as_of"],
                          "final_status": final_status, "setup_status": "NOT_REACHED",
                          "agents": [], "risk_decision": None, "paper": {},
                          "warnings": [], "error": error}
            self.db.execute("INSERT INTO review_reports(run_id,slot_key,payload) VALUES(?,?,?) "
                            "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload",
                            (row["run_id"], key, safe_json({**review, "provider_health": {
                                "market": self.get_state("market_data_provider"),
                                "ai": self.get_state("ai_provider"),
                                "macro": self.get_state("macro_provider")}})))
            self._event(completed_at, row["run_id"], row["symbol"], "runtime",
                        "RUN_COMPLETED" if status == "COMPLETED" else "RUN_FAILED", "INFO" if status == "COMPLETED" else "ERROR",
                        {"final_status": final_status, "error": error})
            if status == "COMPLETED" and final_status not in {"NO_DATA", "STALE_DATA", "ERROR", "PROVIDER_FAILURE"}:
                self.set_state("last_success", utc(completed_at))
            self.set_state("last_run", utc(completed_at))
            return True

    def set_state(self, key, value):
        self.db.execute("INSERT INTO system_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def get_state(self, key):
        row = self.db.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def heartbeat(self, at, scheduler_status):
        with self.transaction():
            self.set_state("heartbeat", utc(at))
            self.set_state("scheduler", scheduler_status)

    def run(self, key):
        row = self.db.execute("SELECT * FROM runs WHERE slot_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, symbol=None):
        row = self.db.execute("SELECT * FROM runs WHERE (? IS NULL OR symbol=?) ORDER BY started_at DESC LIMIT 1", (symbol, symbol)).fetchone()
        return dict(row) if row else None

    def journal(self, *, symbol=None, run_id=None, source=None, event=None, date=None, limit=500):
        sql = "SELECT * FROM journal WHERE (? IS NULL OR symbol=?) AND (? IS NULL OR run_id=?) AND (? IS NULL OR source=?) AND (? IS NULL OR event_type=?) AND (? IS NULL OR substr(timestamp,1,10)=?) ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.db.execute(sql, (symbol, symbol, run_id, run_id, source, source, event, event, date, date, limit))]

    def capture_notifications(self, *, run_id=None):
        """Persist eligible journal events before a caller may deliver them."""
        from runtime.notifications import NotificationEvent
        from runtime.scheduler import session_names
        selected = {"RUN_FAILED", "STATE_INCONSISTENCY", "RECOVERY_STARTED",
                    "RECOVERY_COMPLETED", "PROVIDER_FAILURE", "AUTH_ERROR",
                    "ENTITLEMENT_ERROR", "RATE_LIMITED", "SETUP_VALID_SETUP",
                    "RISK_REJECTED", "ORDER_SUBMITTED", "POSITION_OPENED",
                    "POSITION_CLOSED", "DAILY_SUMMARY", "MACRO_HIGH_IMPORTANCE",
                    "MACRO_HIGH_RELEVANCE"}
        events = []
        with self.transaction():
            rows = self.db.execute("SELECT * FROM journal WHERE (? IS NULL OR run_id=?) ORDER BY id",
                                   (run_id, run_id)).fetchall()
            for row in rows:
                if row["event_type"] not in selected:
                    continue
                event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"journal:{row['id']}"))
                if self.db.execute("SELECT 1 FROM notification_events WHERE event_id=?", (event_id,)).fetchone():
                    continue
                review = self.review_report(row["run_id"]) if row["run_id"] else None
                agents = tuple({"agent": a["agent"], "status": a["status"],
                                "reasoning_summary": a["reasoning_summary"]}
                               for a in review.get("agents", ())) if review else ()
                evidence = tuple(dict.fromkeys(e for a in review.get("agents", ())
                                                for e in a.get("evidence_received", ())
                                                if e is not None)) if review else ()
                if row["event_type"] in {"MACRO_HIGH_IMPORTANCE", "MACRO_HIGH_RELEVANCE"}:
                    macro_id = json.loads(row["payload"]).get("event_id")
                    if macro_id:
                        evidence = tuple(dict.fromkeys((*evidence, macro_id)))
                entity = row["source"]
                event = NotificationEvent(
                    "1.0", event_id, row["run_id"] or "system", row["timestamp"],
                    row["severity"], row["event_type"], row["symbol"],
                    session_names(parse_utc(row["timestamp"])), agents,
                    review.get("risk_decision") if review else None,
                    (entity,) if row["event_type"] in {"ORDER_SUBMITTED", "POSITION_OPENED"} else (),
                    (entity,) if row["event_type"] == "POSITION_CLOSED" else (), evidence,
                    json.loads(row["payload"]) if row["event_type"] == "DAILY_SUMMARY" else None)
                self.db.execute("INSERT INTO notification_events VALUES(?,?,?)",
                                (event_id, row["id"], safe_json(event.payload())))
                events.append(event)
        return events

    def notification_events(self, *, run_id=None):
        rows = self.db.execute("SELECT payload FROM notification_events ORDER BY journal_id").fetchall()
        events = [json.loads(row[0]) for row in rows]
        return [event for event in events if run_id is None or event["run_id"] == run_id]

    def claim_notification_delivery(self, event_id, at):
        """At-most-once external attempt; a crash may leave an alert unsent."""
        with self.transaction():
            if not self.db.execute("SELECT 1 FROM notification_events WHERE event_id=?", (event_id,)).fetchone():
                raise ValueError("notification must be persisted before delivery")
            result = self.db.execute("INSERT OR IGNORE INTO notification_deliveries(event_id,status,attempted_at) VALUES(?,?,?)",
                                     (event_id, "CLAIMED", utc(at)))
            return result.rowcount == 1

    def complete_notification_delivery(self, event_id, at, error_type=None):
        with self.transaction():
            self.db.execute("UPDATE notification_deliveries SET status=?,completed_at=?,error_type=? WHERE event_id=? AND status='CLAIMED'",
                            ("FAILED" if error_type else "DELIVERED", utc(at), error_type, event_id))

    def record_macro_awareness(self, at, run_id, symbol, events):
        with self.transaction():
            for event in events:
                source_high = event.get("impact") == "HIGH"
                policy_high = (event.get("data_quality") or {}).get("policy_relevance") == "HIGH"
                if not (source_high or policy_high) or event.get("window") not in {
                    "UPCOMING", "ACTIVE_WINDOW", "DATE_ONLY_UPCOMING", "DATE_ONLY_TODAY"}:
                    continue
                event_id = event.get("event_id")
                if not event_id:
                    continue
                inserted = self.db.execute("INSERT OR IGNORE INTO macro_awareness VALUES(?,?,?)",
                                           (event_id, symbol, run_id)).rowcount
                if inserted:
                    self._event(at, run_id, symbol, "macro_news",
                                "MACRO_HIGH_IMPORTANCE" if source_high else "MACRO_HIGH_RELEVANCE", "WARNING",
                                {"event_id": event_id, "event_time_utc": event.get("event_timestamp"),
                                 "event_date": event.get("event_date"),
                                 "impact": "HIGH" if source_high else None,
                                 "policy_relevance": "HIGH" if policy_high else None})

    def save_paper(self, broker, *, owner_key=None, symbol=None):
        account = broker.account
        with self.transaction():
            if owner_key is not None and not self.owns_slot(owner_key, symbol):
                raise RuntimeError("paper persistence without slot ownership")
            self.db.execute("INSERT OR REPLACE INTO paper_accounts VALUES(?,?)", (account.account_id, paper_encode(account)))
            for table, items, id_field in (("paper_orders", broker.orders.values(), "order_id"),
                ("paper_fills", broker.fills.values(), "fill_id"),
                ("paper_positions", account.open_positions.values(), "position_id"),
                ("closed_trades", account.closed_trades, "trade_id")):
                for item in items:
                    self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (getattr(item, id_field), paper_encode(item)))
            for trade in account.closed_trades:
                row = self.db.execute("SELECT payload FROM paper_positions WHERE position_id=?", (trade.position_id,)).fetchone()
                if row:
                    position = paper_decode("PaperPosition", row[0])
                    position.status = "CLOSED"
                    self.db.execute("UPDATE paper_positions SET payload=? WHERE position_id=?", (paper_encode(position), trade.position_id))
            for event in broker.journal:
                self._event(event.timestamp or datetime.now(timezone.utc), event.run_id, event.symbol,
                            event.entity_id, event.event_type, "INFO", event.details)
            run_ids = {item.run_id for item in broker.orders.values()}
            run_ids.update(item.run_id for item in broker.fills.values())
            run_ids.update(item.run_id for item in account.closed_trades)
            for run_id in run_ids:
                row = self.db.execute("SELECT payload FROM review_reports WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    continue
                review = json.loads(row[0])
                review["paper"] = {
                    "orders": [{"order_id": o.order_id, "status": o.status} for o in broker.orders.values() if o.run_id == run_id],
                    "fills": [f.fill_id for f in broker.fills.values() if f.run_id == run_id],
                    "open_positions": [p.position_id for p in account.open_positions.values() if p.run_id == run_id],
                    "closed_trades": [{"trade_id": t.trade_id, "net_pnl": t.net_pnl} for t in account.closed_trades if t.run_id == run_id],
                    "equity": account.equity, "realized_pnl": account.realized_pnl,
                    "unrealized_pnl": account.unrealized_pnl}
                self.db.execute("UPDATE review_reports SET payload=? WHERE run_id=?", (safe_json(review), run_id))
        broker.journal.clear()

    def review_report(self, run_id):
        row = self.db.execute("SELECT payload FROM review_reports WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_review(self, symbol=None):
        row = self.db.execute("SELECT rr.payload FROM review_reports rr JOIN runs r ON r.slot_key=rr.slot_key "
                              "WHERE (? IS NULL OR r.symbol=?) ORDER BY r.started_at DESC LIMIT 1",
                              (symbol, symbol)).fetchone()
        return json.loads(row[0]) if row else None

    def load_paper(self, account_id):
        row = self.db.execute("SELECT payload FROM paper_accounts WHERE account_id=?", (account_id,)).fetchone()
        account = paper_decode("PaperAccount", row[0]) if row else None
        orders = {o.order_id: o for o in (paper_decode("PaperOrder", r[0]) for r in self.db.execute("SELECT payload FROM paper_orders"))}
        fills = {f.fill_id: f for f in (paper_decode("PaperFill", r[0]) for r in self.db.execute("SELECT payload FROM paper_fills"))}
        if account:
            positions = [paper_decode("PaperPosition", r[0]) for r in self.db.execute("SELECT payload FROM paper_positions WHERE json_extract(payload,'$.status')='OPEN'")]
            if len({p.symbol for p in positions}) != len(positions):
                raise RuntimeError("duplicate open paper position")
            account.open_positions = {p.symbol: p for p in positions}
            account.closed_trades = [paper_decode("ClosedTrade", r[0]) for r in self.db.execute("SELECT payload FROM closed_trades")]
        return account, orders, fills

    def recover(self, at, stale_after_seconds=120):
        with self.transaction():
            self._event(at, None, None, "runtime", "RECOVERY_STARTED")
            rows = [row for row in self.db.execute("SELECT slot_key,symbol,run_id,started_at FROM runs WHERE status='RUNNING'")
                    if (at - parse_utc(row["started_at"])).total_seconds() >= stale_after_seconds]
            for row in rows:
                run_id = row["run_id"] or str(uuid.uuid5(uuid.NAMESPACE_URL, row["slot_key"]))
                self.db.execute("UPDATE runs SET run_id=?,status='FAILED',final_status='ERROR',completed_at=?,error='interrupted_run' WHERE slot_key=?", (run_id, utc(at), row["slot_key"]))
                self.db.execute("DELETE FROM symbol_locks WHERE slot_key=?", (row["slot_key"],))
                review_row = self.db.execute("SELECT payload FROM review_reports WHERE run_id=?", (run_id,)).fetchone()
                if review_row:
                    review = json.loads(review_row[0])
                    review.update(final_status="ERROR", error="interrupted_run")
                    self.db.execute("UPDATE review_reports SET payload=? WHERE run_id=?", (safe_json(review), run_id))
                else:
                    review = {"schema_version": "1.0", "run_id": run_id, "slot_key": row["slot_key"],
                              "symbol": row["symbol"], "as_of": None, "final_status": "ERROR",
                              "setup_status": "NOT_REACHED", "agents": [], "risk_decision": None,
                              "paper": {}, "warnings": [], "error": "interrupted_run"}
                    self.db.execute("INSERT INTO review_reports VALUES(?,?,?)", (run_id, row["slot_key"], safe_json(review)))
                self._event(at, run_id, row["symbol"], "runtime", "STATE_INCONSISTENCY", "ERROR", {"slot_key": row["slot_key"], "reason": "interrupted_run"})
            self._event(at, None, None, "runtime", "RECOVERY_COMPLETED", "INFO", {"unfinished_runs": len(rows)})
            self.set_state("recovery_at", utc(at))
        return len(rows)

    def save_snapshot(self, symbol, payload):
        with self.transaction():
            self.db.execute("INSERT INTO ui_snapshots(symbol,payload) VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload", (symbol, safe_json(payload)))

    def load_snapshot(self, symbol):
        row = self.db.execute("SELECT payload FROM ui_snapshots WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else None
