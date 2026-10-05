"""SYSTEM HEALTH sidecar SQLite database (V2 Phase 1), isolated from trading_floor.db.

A separate file with its own application_id and version. It holds only passive health
records, never touches the trading database, and refuses to open any database that is not
a SYSTEM HEALTH sidecar (including the trading DB): such a file is never written to.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

HEALTH_APPLICATION_ID = 0x56324853  # "V2HS"
HEALTH_SCHEMA_VERSION = 1
HEALTH_TABLES = frozenset({"health_schema_info", "component_health", "health_observations"})
HEALTH_SCHEMA = """
CREATE TABLE health_schema_info(version INTEGER NOT NULL);
CREATE TABLE component_health(
  component TEXT NOT NULL, provider TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(component,provider));
CREATE TABLE health_observations(
  observation_id TEXT PRIMARY KEY, component TEXT NOT NULL, provider TEXT NOT NULL,
  observed_at TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL);
"""


class HealthStoreError(RuntimeError):
    """The sidecar is missing, incompatible or corrupt. Nothing is repaired or recreated."""


class HealthStore:
    def __init__(self, path, *, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly and not self.path.exists():
            raise HealthStoreError("SYSTEM HEALTH database missing")
        if not readonly:
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
            raise HealthStoreError("SYSTEM HEALTH database unreadable") from exc
        if not tables and application_id == 0:
            if self.readonly:
                raise HealthStoreError("SYSTEM HEALTH database not initialized")
            self.db.executescript("BEGIN IMMEDIATE;" + HEALTH_SCHEMA +
                                  f"INSERT INTO health_schema_info(version) VALUES({HEALTH_SCHEMA_VERSION});"
                                  f"PRAGMA application_id={HEALTH_APPLICATION_ID};COMMIT;")
            return
        # Validation only: an existing file is never altered here.
        if application_id != HEALTH_APPLICATION_ID or tables != HEALTH_TABLES:
            raise HealthStoreError("not a SYSTEM HEALTH database (refusing to use it)")
        rows = self.db.execute("SELECT version FROM health_schema_info").fetchall()
        if len(rows) != 1 or rows[0][0] != HEALTH_SCHEMA_VERSION:
            raise HealthStoreError("incompatible SYSTEM HEALTH schema version")
        if self.db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise HealthStoreError("SYSTEM HEALTH database integrity check failed")

    @contextmanager
    def transaction(self):
        if self.readonly:
            raise HealthStoreError("SYSTEM HEALTH database opened read-only")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()
