"""MARKET EVIDENCE sidecar SQLite database (V2 Phase 2), isolated from trading_floor.db.

A separate file with its own application_id and schema version. It holds only committed
market evidence (closed bars) and evidence anomalies; it never touches the trading database
and refuses to open any database that is not a MARKET EVIDENCE sidecar (including the trading
DB): such a file is never written to. Nothing is repaired or recreated automatically.

A path named like the trading DB is refused before any connection, whether or not the file
exists or is still empty (an empty trading DB has no application_id yet). An existing sidecar is
accepted only when its actual schema (columns, primary/unique keys, no triggers) matches the
contract below; otherwise it is refused on open, never repaired.
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


def _is_trading_db_name(path):
    """Case-insensitive, after resolving links/'..'; Windows ignores trailing dots/spaces and an
    alternate-data-stream suffix, so those cannot disguise the trading DB name either."""
    try:
        names = {path.name, path.resolve().name}
    except OSError as exc:
        raise EvidenceStoreError("cannot verify that the path is not the trading DB") from exc
    return any(name.split(":")[0].rstrip(" .").casefold() == TRADING_DB_NAME for name in names)


def _shares_file_with_trading_db(path):
    """An existing file that is the same file (e.g. a hard link) as a sibling trading DB."""
    if not path.exists():
        return False
    try:
        return any(_is_trading_db_name(sibling) and sibling.is_file() and path.samefile(sibling)
                   for sibling in path.resolve().parent.iterdir())
    except OSError as exc:
        raise EvidenceStoreError("cannot verify that the path is not the trading DB") from exc


def _schema_contract(db):
    """Semantic schema via SQLite metadata: per table, columns (declared type, NOT NULL, part of
    the PRIMARY KEY), and the column sets of full UNIQUE/PRIMARY KEY indexes with their
    collations. Column order, key column order and SQL formatting are irrelevant."""
    contract = {}
    for table in sorted(EVIDENCE_TABLES):
        columns = frozenset((row[1], row[2].upper(), row[3], bool(row[5]))
                            for row in db.execute(f"PRAGMA table_info({table})"))
        unique = set()
        for index in db.execute(f"PRAGMA index_list({table})").fetchall():
            if index[2] and not index[4]:  # Unique and not partial: enforced for every row.
                keys = frozenset((row[2], (row[4] or "BINARY").upper())
                                 for row in db.execute(f"PRAGMA index_xinfo({index[1]!r})") if row[5])
                unique.add(keys)
        contract[table] = (columns, frozenset(unique))
    return contract


def _expected_contract():
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(EVIDENCE_SCHEMA)
        return _schema_contract(db)
    finally:
        db.close()


EXPECTED_CONTRACT = _expected_contract()


class EvidenceStore:
    def __init__(self, path, *, readonly=False):
        self.path = Path(path)
        self.readonly = readonly
        if _is_trading_db_name(self.path) or _shares_file_with_trading_db(self.path):
            raise EvidenceStoreError("refusing to use the trading DB path as a MARKET EVIDENCE database")
        if readonly and not self.path.exists():
            raise EvidenceStoreError("MARKET EVIDENCE database missing")
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
            contract = _schema_contract(self.db)
            triggers = self.db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
            rows = self.db.execute("SELECT version FROM evidence_schema_info").fetchall()
            check = self.db.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as exc:
            raise EvidenceStoreError("MARKET EVIDENCE database unreadable") from exc
        if contract != EXPECTED_CONTRACT or triggers:
            raise EvidenceStoreError("MARKET EVIDENCE schema does not match the required contract")
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
