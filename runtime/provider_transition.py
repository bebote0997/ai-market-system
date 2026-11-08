"""Explicit, single-field AI provider transition. Not applied by startup or preflight.

``RuntimeConfig.fingerprint`` still includes ``ai_provider_mode``. A different mode does not resume an
existing experiment unless an operator has recorded the from/to payloads, the account was flat, and the
only changed field is ``ai_provider_mode``. Any other mismatch stays blocked. Historical ``run_metadata``
rows are not rewritten. This module does not open, close, or send an order, and it does not read the
broker book. The runner's existing request poll is what calls ``record_provider_transition`` after that
book has been counted. A valid chain resumes while the process is still on the old mode, so the row can
be written before ``AI_FLOOR_AI_PROVIDER`` changes.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import sqlite3


FIELD = "ai_provider_mode"
ALLOWED = frozenset({"deterministic", "openai", "xai"})
FLAT_KEYS = ("paper_open", "paper_pending", "broker_live")
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    from_payload TEXT NOT NULL,
    to_payload TEXT NOT NULL,
    from_fingerprint TEXT NOT NULL,
    to_fingerprint TEXT NOT NULL,
    field TEXT NOT NULL,
    from_value TEXT NOT NULL,
    to_value TEXT NOT NULL,
    paper_open INTEGER NOT NULL,
    paper_pending INTEGER NOT NULL,
    broker_live INTEGER NOT NULL,
    git_commit TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    actor TEXT NOT NULL,
    operator_note TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS provider_transitions_no_update
BEFORE UPDATE ON provider_transitions
BEGIN SELECT RAISE(ABORT, 'provider_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS provider_transitions_no_delete
BEFORE DELETE ON provider_transitions
BEGIN SELECT RAISE(ABORT, 'provider_transitions is append-only'); END;
"""


def fingerprint_of(payload):
    """Same bytes as ``RuntimeConfig.fingerprint`` for this payload."""
    if not isinstance(payload, dict):
        raise ValueError("fingerprint payload must be an object")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _canonical(payload):
    return json.loads(json.dumps(payload, sort_keys=True))


def changed_fields(left, right):
    keys = set(left) | set(right)
    return {key for key in keys if left.get(key) != right.get(key)}


def _check_step(record):
    """Raise ValueError when a stored or proposed step is not a single approved provider change."""
    left, right = _canonical(record["from_payload"]), _canonical(record["to_payload"])
    if changed_fields(left, right) != {FIELD}:
        raise ValueError("provider transition may change only ai_provider_mode")
    origin, dest = left.get(FIELD), right.get(FIELD)
    if origin not in ALLOWED or dest not in ALLOWED or origin == dest:
        raise ValueError("provider transition values are not an approved pair")
    if record.get("field") not in (None, FIELD) or record.get("from_value", origin) != origin:
        raise ValueError("provider transition field does not match the payload")
    if record.get("to_value", dest) != dest:
        raise ValueError("provider transition field does not match the payload")
    for key in FLAT_KEYS:
        if type(record.get(key)) is not int or record[key] != 0:
            raise ValueError("provider transition requires a flat account")
    if fingerprint_of(left) != record.get("from_fingerprint", fingerprint_of(left)):
        raise ValueError("provider transition from fingerprint does not match its payload")
    if fingerprint_of(right) != record.get("to_fingerprint", fingerprint_of(right)):
        raise ValueError("provider transition to fingerprint does not match its payload")
    return left, right


def _flat(flat_counts):
    if not isinstance(flat_counts, dict):
        raise ValueError("provider transition requires a flat account")
    counts = {}
    for key in FLAT_KEYS:
        value = flat_counts.get(key)
        if type(value) is not int or value != 0:
            raise ValueError("provider transition requires a flat account")
        counts[key] = value
    return counts


def _equity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError("starting equity required")
    return float(value)


def record_provider_transition(connection, *, from_payload, to_payload, flat_counts, operator_note,
                               git_commit, starting_equity, actor, recorded_at=None):
    """Append one immutable step, or no-op when that exact step is already the tail.

    Refuses a non-zero book, any diff other than the provider mode, or a step that does not continue the
    chain. Two callers serialize on ``BEGIN IMMEDIATE``. Preflight never calls this.
    """
    counts = _flat(flat_counts)
    if not isinstance(operator_note, str) or not operator_note.strip():
        raise ValueError("operator note required")
    if not isinstance(git_commit, str) or not _SHA.fullmatch(git_commit):
        raise ValueError("freeze sha required")
    if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 64:
        raise ValueError("actor required")
    equity = _equity(starting_equity)
    proposed = {"from_payload": from_payload, "to_payload": to_payload, **counts, "field": FIELD,
                "from_value": from_payload.get(FIELD) if isinstance(from_payload, dict) else None,
                "to_value": to_payload.get(FIELD) if isinstance(to_payload, dict) else None}
    left, right = _check_step({**proposed, "from_fingerprint": fingerprint_of(_canonical(from_payload)),
                               "to_fingerprint": fingerprint_of(_canonical(to_payload))})
    when = recorded_at or datetime.now(timezone.utc)
    if not isinstance(when, datetime) or when.tzinfo is None:
        raise ValueError("timezone-aware recorded_at required")
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    origin_fp, dest_fp = fingerprint_of(left), fingerprint_of(right)
    connection.executescript(_SCHEMA)
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = load_transitions(connection)
        if existing:
            last = existing[-1]
            if last["from_payload"] == left and last["to_payload"] == right:
                connection.execute("ROLLBACK")
                return {"status": "duplicate", "from_fingerprint": origin_fp, "to_fingerprint": dest_fp}
            if last["to_payload"] != left:
                connection.execute("ROLLBACK")
                raise ValueError("provider transition does not continue the chain")
        connection.execute(
            "INSERT INTO provider_transitions(recorded_at, from_payload, to_payload, from_fingerprint, "
            "to_fingerprint, field, from_value, to_value, paper_open, paper_pending, broker_live, "
            "git_commit, starting_equity, actor, operator_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (stamp, json.dumps(left, sort_keys=True), json.dumps(right, sort_keys=True),
             origin_fp, dest_fp, FIELD, left[FIELD], right[FIELD], 0, 0, 0, git_commit, equity,
             actor.strip(), operator_note.strip()))
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    return {"status": "recorded", "from_fingerprint": origin_fp, "to_fingerprint": dest_fp}


def load_transitions(connection):
    """Rows already recorded, oldest first. Missing table means none. Does not create the table."""
    names = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'provider_transitions'")}
    if not names:
        return ()
    found = []
    for row in connection.execute(
            "SELECT from_payload, to_payload, from_fingerprint, to_fingerprint, field, from_value, "
            "to_value, paper_open, paper_pending, broker_live, git_commit, starting_equity, actor, "
            "operator_note, recorded_at FROM provider_transitions ORDER BY id"):
        found.append({
            "from_payload": json.loads(row[0]), "to_payload": json.loads(row[1]),
            "from_fingerprint": row[2], "to_fingerprint": row[3], "field": row[4],
            "from_value": row[5], "to_value": row[6], "paper_open": row[7], "paper_pending": row[8],
            "broker_live": row[9], "git_commit": row[10], "starting_equity": row[11], "actor": row[12],
            "operator_note": row[13], "recorded_at": row[14]})
    return tuple(found)


def _aware(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def destination_committed(rows, dest_payload, recorded_at):
    """True when a run stored at or after ``recorded_at`` used the destination fingerprint.

    Rows are ``(git, fingerprint, equity)`` or ``(git, fingerprint, equity, started_at)``. A row without
    ``started_at`` cannot prove it happened after the step, so it does not commit the destination.
    A missing ``recorded_at`` fails closed: the step is treated as already committed.
    """
    recorded = _aware(recorded_at)
    if recorded is None:
        return True
    dest_fp = fingerprint_of(_canonical(dest_payload))
    for row in rows:
        if row[1] != dest_fp or len(row) < 4:
            continue
        started = _aware(row[3])
        if started is not None and started >= recorded:
            return True
    return False


def transition_resumes(rows, *, freeze, freeze_ok, started, account_present, current_payload,
                       starting_equity, records):
    """True only for an explicit chain that explains every stored fingerprint.

    With no records this returns False: the caller keeps the original all-rows-match rule.
    A bad record does not get ignored.
    """
    if not records or not freeze_ok or not started or not account_present or not freeze:
        return False
    try:
        steps = [_check_step(record) for record in records]
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    for previous, current in zip(steps, steps[1:]):
        if previous[1] != current[0]:
            return False
    current = _canonical(current_payload)
    # History may contain every fingerprint on the chain. The live config may be the tail, or the
    # immediate predecessor only until a run has been stored on the tail after this step. An older
    # node is not a silent rollback.
    last_from, last_to = steps[-1]
    committed = destination_committed(rows, last_to, records[-1].get("recorded_at"))
    if current != last_to and not (current == last_from and not committed):
        return False
    allowed = {fingerprint_of(steps[0][0])}
    allowed.update(fingerprint_of(right) for _left, right in steps)
    if fingerprint_of(steps[0][0]) not in {row[1] for row in rows}:
        return False  # the pre-transition history is still required
    if not rows:
        return False
    for record in records:
        if record.get("git_commit") != freeze or record.get("starting_equity") != starting_equity:
            return False
    for row in rows:
        git_commit, fingerprint, equity = row[0], row[1], row[2]
        if git_commit != freeze or fingerprint not in allowed or equity != starting_equity:
            return False
    return True


def entry_fence_required(connection):
    """True while the latest step's destination has not yet been used by a run. A bad chain stays fenced."""
    records = load_transitions(connection)
    if not records:
        return False
    try:
        steps = [_check_step(record) for record in records]
    except (ValueError, TypeError, json.JSONDecodeError):
        return True
    for previous, current in zip(steps, steps[1:]):
        if previous[1] != current[0]:
            return True
    found = connection.execute(
        "SELECT m.config_fingerprint, r.started_at FROM run_metadata m "
        "LEFT JOIN runs r ON r.slot_key = m.slot_key").fetchall()
    rows = [(None, row[0], None, row[1]) for row in found]
    return not destination_committed(rows, steps[-1][1], records[-1].get("recorded_at"))
