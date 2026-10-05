"""MARKET EVIDENCE sidecar SQLite database (V2 Phase 2), isolated from trading_floor.db.

A separate file with its own application_id and schema version. It holds only committed
market evidence (closed bars) and evidence anomalies; it never touches the trading database
and refuses to open any database that is not a MARKET EVIDENCE sidecar (including the trading
DB): such a file is never written to. Nothing is repaired or recreated automatically.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

EVIDENCE_APPLICATION_ID = 0x56324D45  # "V2ME"
EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_TABLES = frozenset({"evidence_schema_info", "market_evidence", "evidence_anomalies"})
# The V1 trading database file name. A new sidecar is never created under this name, so a
# misconfigured path cannot pre-empt the trading database (which then could not initialize).
TRADING_DB_NAME = "trading_floor.db"
EVIDENCE_SCHEMA = """
CREATE TABLE evidence_schema_info(version INTEGER NOT NULL);
CREATE TABLE market_evidence(
  symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_start TEXT NOT NULL,
  stream_seq INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
  PRIMARY KEY(symbol,timeframe,bar_start), UNIQUE(symbol,timeframe,stream_seq));
CREATE TABLE evidence_anomalies(
  anomaly_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
  kind TEXT NOT NULL, bar_start TEXT NOT NULL, payload TEXT NOT NULL);
"""


class EvidenceStoreError(RuntimeError):
    """The sidecar is missing, incompatible or corrupt. Nothing is repaired or recreated."""


class EvidenceStore:
    def __init__(self, path, *, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly and not self.path.exists():
            raise EvidenceStoreError("MARKET EVIDENCE database missing")
        if not readonly:
            if self.path.name == TRADING_DB_NAME and not self.path.exists():
                raise EvidenceStoreError("refusing to create a MARKET EVIDENCE database under the trading DB name")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        mode = "ro" if readonly else "rwc"
        self.db = sqlite3.connect(f"file:{self.path.resolve().as_posix()}?mode={mode}", uri=True,
                                  timeout=10, isolation_level=None)
        try:
            self.db.execute("PRAGMA busy_timeout=10000")
            self._open()
        except BaseException:
            self.db.close()
            raise

    def _open(self):
        try:
            tables = {r[0] for r in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise EvidenceStoreError("MARKET EVIDENCE database unreadable") from exc
        if not tables and application_id == 0:
            if self.readonly:
                raise EvidenceStoreError("MARKET EVIDENCE database not initialized")
            self.db.executescript("BEGIN IMMEDIATE;" + EVIDENCE_SCHEMA +
                                  f"INSERT INTO evidence_schema_info(version) VALUES({EVIDENCE_SCHEMA_VERSION});"
                                  f"PRAGMA application_id={EVIDENCE_APPLICATION_ID};COMMIT;")
            return
        # Validation only: an existing file is never altered here.
        if application_id != EVIDENCE_APPLICATION_ID or tables != EVIDENCE_TABLES:
            raise EvidenceStoreError("not a MARKET EVIDENCE database (refusing to use it)")
        try:
            rows = self.db.execute("SELECT version FROM evidence_schema_info").fetchall()
            check = self.db.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as exc:
            raise EvidenceStoreError("MARKET EVIDENCE database unreadable") from exc
        if len(rows) != 1 or rows[0][0] != EVIDENCE_SCHEMA_VERSION:
            raise EvidenceStoreError("incompatible MARKET EVIDENCE schema version")
        if check != [("ok",)]:
            raise EvidenceStoreError("MARKET EVIDENCE database integrity check failed")

    @contextmanager
    def transaction(self):
        if self.readonly:
            raise EvidenceStoreError("MARKET EVIDENCE database opened read-only")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()
