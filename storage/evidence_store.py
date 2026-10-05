"""MARKET EVIDENCE sidecar SQLite database (V2 Phase 2), isolated from trading_floor.db.

A separate file with its own application_id and schema version. It holds only committed
market evidence (closed bars) and evidence anomalies; it never touches the trading database
and refuses to open any database that is not a MARKET EVIDENCE sidecar (including the trading
DB): such a file is never written to. Nothing is repaired or recreated automatically.

Target identity: the path is resolved ONCE to an absolute filesystem target; that target is
what is validated (trading DB name/identity) and exactly what SQLite opens. Read-write opens it
as a plain filename (no URI parsing); read-only opens it through a percent-encoded file URI
(``Path.as_uri``), so ``#``, ``?``, ``%`` and spaces stay part of the filename. After connecting,
SQLite's own ``PRAGMA database_list`` must report that same target before anything is written.

A path named like the trading DB is refused before any connection, whether or not the file
exists or is still empty (an empty trading DB has no application_id yet).

Schema contract: an existing sidecar is accepted only when its effective schema cannot make a
canonical write behave differently (see ``_schema_contract``); otherwise it is refused on open,
before any ingestion, and is never repaired.
"""
from contextlib import contextmanager
from pathlib import Path
import re
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


def _target(path):
    """The single filesystem object that is both validated and opened."""
    try:
        return path.resolve()
    except OSError as exc:
        raise EvidenceStoreError("cannot resolve the MARKET EVIDENCE database path") from exc


def _is_trading_db_name(*paths):
    """Case-insensitive, after resolving links/'..'; Windows ignores trailing dots/spaces and an
    alternate-data-stream suffix, so those cannot disguise the trading DB name either."""
    return any(path.name.split(":")[0].rstrip(" .").casefold() == TRADING_DB_NAME for path in paths)


def _shares_file_with_trading_db(target):
    """An existing file that is the same file (e.g. a hard link) as a sibling trading DB."""
    if not target.exists():
        return False
    try:
        return any(_is_trading_db_name(sibling) and sibling.is_file() and target.samefile(sibling)
                   for sibling in target.parent.iterdir())
    except OSError as exc:
        raise EvidenceStoreError("cannot verify that the path is not the trading DB") from exc


# --- schema contract ------------------------------------------------------------------------------
# Strategy: SQLite metadata wherever it exists (table_xinfo, index_list, index_xinfo,
# foreign_key_list, sqlite_master triggers), plus the smallest extra inspection for constraints
# that metadata does not expose: a keyword scan of each table's CREATE statement with string
# literals, quoted identifiers and comments removed first. A keyword below in a table definition
# can reject, rewrite or suppress a canonical write, so its presence refuses the store.
_SQL_NOISE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|\[[^\]]*\]|--[^\n]*|/\*.*?(?:\*/|$)",
                        re.S)
BEHAVIOR_KEYWORDS = frozenset({
    "CHECK",          # Rejects rows.
    "CONFLICT",       # ON CONFLICT REPLACE/IGNORE/... silently replaces or drops rows.
    "REFERENCES",     # Foreign keys (also read from PRAGMA foreign_key_list).
    "GENERATED", "STORED", "VIRTUAL",  # Generated columns / virtual tables (also table_xinfo hidden).
    "STRICT", "WITHOUT",  # Table options the canonical schema does not use (type rules, rowid).
    "AUTOINCREMENT",
})


def _affinity(declared):
    """SQLite column affinity rules (https://sqlite.org/datatype3.html, section 3.1)."""
    declared = (declared or "").upper()
    if "INT" in declared:
        return "INTEGER"
    if any(token in declared for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not declared or "BLOB" in declared:
        return "BLOB"
    if any(token in declared for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _behavior_keywords(sql):
    words = [w.upper() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", _SQL_NOISE.sub(" ", sql or ""))]
    found = set(words) & BEHAVIOR_KEYWORDS
    found |= {f"COLLATE {following or '?'}" for word, following in zip(words, words[1:] + [""])
              if word == "COLLATE" and following != "BINARY"}  # Non-binary (or quoted) collation.
    return frozenset(found)


def _schema_contract(db):
    """Per table, everything that can change a canonical write:
    - columns: name, affinity, NOT NULL, primary-key membership, hidden/generated flag;
    - every UNIQUE/PRIMARY KEY index: key columns with collation, and whether it is partial;
    - unsafe non-unique indexes (partial, or on expressions: evaluated, and able to fail, on write);
    - foreign keys and behavior keywords of the table definition.
    Harmless differences (SQL formatting/case, column and key order, declared type spelling with
    the same affinity, plain non-unique column indexes) do not change the contract."""
    contract = {}
    for table in sorted(EVIDENCE_TABLES):
        columns = frozenset((row[1], _affinity(row[2]), row[3], bool(row[5]), row[6])
                            for row in db.execute(f"PRAGMA table_xinfo({table})"))
        unique, unsafe = set(), set()
        for _, name, is_unique, _, partial in db.execute(f"PRAGMA index_list({table})").fetchall():
            keys = [(row[1], row[2], (row[4] or "BINARY").upper())
                    for row in db.execute("SELECT * FROM pragma_index_xinfo(?)", (name,)) if row[5]]
            if is_unique:
                unique.add((frozenset((column, collation) for _, column, collation in keys), bool(partial)))
            elif partial or any(cid < 0 for cid, _, _ in keys):
                unsafe.add(name)
        foreign_keys = len(db.execute(f"PRAGMA foreign_key_list({table})").fetchall())
        sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        contract[table] = (columns, frozenset(unique), frozenset(unsafe), foreign_keys,
                           _behavior_keywords(sql[0] if sql else None))
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
        target = _target(self.path)
        if _is_trading_db_name(self.path, target) or _shares_file_with_trading_db(target):
            raise EvidenceStoreError("refusing to use the trading DB path as a MARKET EVIDENCE database")
        if readonly and not target.is_file():
            raise EvidenceStoreError("MARKET EVIDENCE database missing")
        try:
            if readonly:  # mode=ro requires a URI; pathlib percent-encodes the target.
                self.db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10,
                                          isolation_level=None)
            else:  # A plain filename: no URI parsing of the path at all.
                target.parent.mkdir(parents=True, exist_ok=True)
                self.db = sqlite3.connect(str(target), uri=False, timeout=10, isolation_level=None)
        except (sqlite3.Error, OSError) as exc:
            raise EvidenceStoreError("MARKET EVIDENCE database cannot be opened") from exc
        try:
            self._verify_target(target)
            self.db.execute("PRAGMA busy_timeout=10000")
            self._open()
        except BaseException:
            self.db.close()
            raise

    def _verify_target(self, target):
        """SQLite's actual main database file must be the validated target (nothing written yet)."""
        try:
            opened = next((row[2] for row in self.db.execute("PRAGMA database_list") if row[1] == "main"), None)
            same = bool(opened) and Path(opened).resolve() == target
        except (sqlite3.Error, OSError) as exc:
            raise EvidenceStoreError("cannot verify the MARKET EVIDENCE database target") from exc
        if not same:
            raise EvidenceStoreError("SQLite target differs from the validated MARKET EVIDENCE path")

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
