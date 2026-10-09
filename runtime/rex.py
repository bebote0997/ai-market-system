"""V2 Phase 8 / P3 REX: run evidence records (design P8.6 5.1; DEC-8.22-d, DEC-8.21b PROVISIONAL, implementation only).

OBSERVABILITY ONLY, alternative B (5.1.3). Behind ``RuntimeConfig.v2_rex`` (``AI_FLOOR_V2_REX``, OFF by default).

- Every economic write commits exactly as without REX (same ``save_paper`` call, arguments and result). Its REX entry is
  written IMMEDIATELY AFTER, in its own transaction. There is NO economic + REX atomicity: a crash between the economic
  commit and the REX row leaves a write without evidence, which the chain verifier classifies as an unevidenced write
  (B-STRICT: CERTIFICATION_INVALID). REX never repairs or backfills it.
- Nothing here changes an order, fill, position, closed trade, equity, risk decision, SL/TP, size, the economic order of
  execution or the result of ``save_paper``. Every REX failure is caught; it is recorded when possible (a
  ``REX_FAILURE`` row and ``complete: false``) and never raised into the cycle. ``save_paper`` exceptions propagate
  unchanged (after being recorded).

Persistence (O-3): rows of the existing ``journal`` table, ``source='rex'``; no schema change. Payload
``{"rex": T, "rex_digest": H("V2REX/1", T)}`` where ``T = cj(rex_encode(record))`` (a string, so the journal's
``safe_json`` never re-encodes or filters its content).
- ``REX_WRITE``: one per ``save_paper`` attempt made through ``RexStore`` (COMMITTED, STALE or RAISED).
- ``REX_RUN``: one per claimed run, written when ``run_cycle`` returns; lists the run's REX_WRITE journal ids.
- ``REX_FAILURE``: best-effort notice of a recording failure (non-economic).

Encoding (``rex_encode``): typed and unambiguous. REAL -> ``{"$f": float.hex(x)}``; Decimal -> ``{"$d": str}``;
aware datetime -> ``{"$t": UTC isoformat}``; dataclass -> ``{"$type": name, field: value...}``; tuple/list -> list;
dict keys must be strings not starting with ``$``; NaN, infinity, naive datetimes and unknown types are rejected
(a REX failure, never an economic one).

Digests: ``edg`` / ``psh`` from ``storage.economic_digest`` (P2a). They are computed ONLY in a read transaction opened
here on a connection with no transaction in progress: never inside a transaction with pending writes, and a caller's
transaction is never committed or rolled back (an open one is a REX failure: nothing is computed).

Integrity (O-6), stated with its limits:
- ``PRAGMA data_version`` (read before and after the write, on the runtime's connection) changes when ANOTHER connection
  commits to the database file. It does NOT see writes made on the same connection.
- ``Connection.total_changes`` read right after ``save_paper`` returns and again inside the post-write snapshot detects a
  write on the same connection in between.
- The persisted CAS state (``Store.paper_state`` read back) must equal the state of the broker that was saved.
These checks narrow, but never close, the trust gap: they cannot see a change made and reverted between two
observations, nor writes before the first or after the last snapshot. Single-writer remains an external attestation.

Observed versus reconstructed (O-5): REX stores values the runtime observed or produced (inputs, outputs, states,
journal ids). Intermediate values that live only inside frozen modules (Risk sizing intermediates, the quantity adapter
transition, the fill-gate clause trace) are NOT stored; the independent oracle reconstructs them and labels them so.
"""
from contextlib import contextmanager
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import math
import numbers
import os
from pathlib import Path

from storage.economic_digest import H, cj, edg, psh

LOG = logging.getLogger("ai_floor.rex")
REX_VERSION = "V2REX/1"
REX_SOURCE = "rex"
REX_WRITE = "REX_WRITE"
REX_RUN = "REX_RUN"
REX_FAILURE = "REX_FAILURE"
REX_MAX_BYTES = 2_000_000  # per REX row; larger -> REX failure (the economic write is unaffected)
AI_RULES_VERSION = "V2_AI_RULES/1"
ECONOMIC_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED",
                             "POSITION_OPENED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"})
# Rule identity (5.1.2.1 / 5.1.2.3, extended with the other decision sources the oracle transcribes).
RULE_FILES = ("ai/orchestrator.py", "ai/contracts.py", "ai/runtime.py", "runtime/gates.py", "runtime/paper_contracts.py",
              "runtime/service.py", "floor/orchestrator.py", "riesgo.py", "core/rr_contract.py", "core/timeframes.py",
              "execution/paper_broker.py", "execution/trade_manager.py")
ROOT = Path(__file__).resolve().parent.parent
_RULE_IDENTITY = None


class RexError(RuntimeError):
    """A REX recording failure. Never raised into the trading cycle."""


class _Encoded:
    """A value already in REX form (encoded when observed, so later mutation cannot change it)."""
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def rex_encode(value):
    """Typed, unambiguous JSON-ready form of ``value`` (see the module docstring)."""
    if isinstance(value, _Encoded):
        return value.value
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal")
        return {"$d": str(value)}
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite REAL")
        return {"$f": number.hex()}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime")
        return {"$t": value.astimezone(timezone.utc).isoformat()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        encoded = {"$type": type(value).__name__}
        for field in dataclasses.fields(value):
            encoded[field.name] = rex_encode(getattr(value, field.name))
        return encoded
    if isinstance(value, (list, tuple)):
        return [rex_encode(item) for item in value]
    if isinstance(value, dict):
        encoded = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.startswith("$"):
                raise ValueError("REX object keys must be strings not starting with '$'")
            encoded[key] = rex_encode(item)
        return encoded
    raise TypeError(f"unsupported REX value: {type(value).__name__}")


def rex_payload(record):
    """The journal payload of ``record``: its canonical text and ``rex_digest``. Raises on oversize."""
    text = cj(rex_encode(record))
    if len(text.encode("utf-8")) > REX_MAX_BYTES:
        raise RexError("REX payload above REX_MAX_BYTES")
    return {"rex": text, "rex_digest": H(REX_VERSION, text)}


def rule_identity(extra_files=(), extra_constants=None):
    """SHA-256 of each rule source file (bytes as checked out) and the decision constants in force. The runtime adds
    the files and constants of modules only it may reference (``extra_files`` / ``extra_constants``)."""
    global _RULE_IDENTITY
    if _RULE_IDENTITY is None:
        from ai.contracts import AI_SCHEMA_VERSION, VALID_AI_STATUSES, VALID_FLOOR_STATUSES, VALID_RECOMMENDATIONS
        files = {}
        for name in (*RULE_FILES, *extra_files):
            path = ROOT / name
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        _RULE_IDENTITY = {"ai_rules_version": AI_RULES_VERSION, "files": files,
                          "ai_schema_version": AI_SCHEMA_VERSION,
                          "valid_floor_statuses": sorted(VALID_FLOOR_STATUSES),
                          "valid_ai_statuses": sorted(VALID_AI_STATUSES),
                          "valid_recommendations": sorted(VALID_RECOMMENDATIONS),
                          "a3_triggers": ["DISAGREE", "REJECT_RECOMMENDATION"], **(extra_constants or {})}
    return _RULE_IDENTITY


def _error_name(exc):
    return type(exc).__name__


class RexRecorder:
    """REX for ONE claimed run. Every public method is failure-isolated: it never raises."""

    def __init__(self, store, *, account_id, run_id, slot_key, symbol, slot, identity=None, clock=None):
        self.store = store  # the real trading Store (never the RexStore proxy)
        self.account_id = account_id
        self.run_id, self.slot_key, self.symbol, self.slot = run_id, slot_key, symbol, slot
        # REX row timestamps use their OWN wall clock, never the runtime clock: an extra runtime clock() call could
        # shift a later decision time under a stepping test clock (inertness, rule 4).
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stages = []
        self.ai_requests = []
        self.ai_observations = []
        self.writes = []
        self.failures = []
        self.write_seq = 0
        self._context = {"stage": None}
        self._attempt = 0
        self.finished = False
        self.identity = {}
        self._guard("identity", lambda: self.identity.update(
            {"rule_identity": rule_identity(), **(identity or {})}))

    # -- failure isolation -------------------------------------------------------------------------------------------
    def _guard(self, where, action):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - REX never changes the cycle
            self.fail(where, exc)
            return None

    def fail(self, where, exc):
        try:
            self.failures.append({"where": str(where), "error_type": _error_name(exc)})
            LOG.warning("run_id=%s symbol=%s component=rex event=REX_FAILURE where=%s error_type=%s",
                        self.run_id, self.symbol, where, _error_name(exc))
            with self.store.transaction():
                self.store._event(self.clock(), self.run_id, self.symbol, REX_SOURCE, REX_FAILURE, "WARNING",
                                  {"where": str(where), "error_type": _error_name(exc)})
        except Exception:  # noqa: BLE001 - a failure to record a failure is only logged
            LOG.warning("run_id=%s component=rex event=REX_FAILURE_NOT_RECORDED", self.run_id)

    # -- stages and context ------------------------------------------------------------------------------------------
    def stage(self, name, data):
        """Record stage ``name`` with ``data`` (encoded now, so later mutation cannot change it)."""
        self._guard(f"stage:{name}", lambda: self.stages.append(_Encoded({"stage": name, **rex_encode(dict(data))})))

    def context(self, stage, data=None):
        """The write context for the next ``save_paper`` attempts (the stage and its exact inputs)."""
        try:
            context = {"stage": stage, **rex_encode(dict(data or {}))}
        except Exception as exc:  # noqa: BLE001 - the attempts under it are then recorded as incomplete
            self.fail(f"context:{stage}", exc)
            context = {"stage": stage, "context_failed": True}
        self._context, self._attempt = context, 0

    def observer(self, event, **data):
        """Callback for the execution modules (O-4): records the exact bar / expected_state they are about to use."""
        stage = {"catch_up_bar": "ST2C", "pending_gate": "ST7", "pending_gate_not_evaluated": "ST7"}.get(event, event)
        if "expected_state" in data:  # the full state is in the REX_WRITE pre_state; keep its exact digest here
            expected = data.pop("expected_state")
            try:
                data["expected_psh"] = psh(expected)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"observer:{event}", exc)
                data["expected_psh"] = None
        self.stage(stage, {"observer": event, **data})
        if event in ("catch_up_bar", "pending_gate"):
            self.context(stage, {"observer": event, **data})

    def ai_request(self, request):
        def record():
            self.ai_requests.append(_Encoded(rex_encode({
                "schema_version": request.schema_version, "run_id": request.run_id, "symbol": request.symbol,
                "as_of": request.as_of, "agent_name": request.agent_name, "role": request.role,
                "prompt_version": request.prompt_version})))
        self._guard("ai_request", record)

    def ai_observation(self, record):
        """Observer for ``ai.runtime.observe_requests``: every request ``call_agent`` handled, emitted or skipped."""
        self._guard("ai_observation", lambda: self.ai_observations.append(_Encoded(rex_encode(dict(record)))))

    # -- economic writes ---------------------------------------------------------------------------------------------
    @contextmanager
    def _read_transaction(self):
        conn = self.store.db
        if conn.in_transaction:
            raise RexError("connection has an open transaction; EDG is never computed inside one")
        conn.execute("BEGIN")
        try:
            if not conn.in_transaction:
                raise RexError("read transaction not confirmed")
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")  # our own read-only transaction: nothing to undo

    def _snapshot(self, journal_after=None):
        with self._read_transaction() as conn:
            data_version = conn.execute("PRAGMA data_version").fetchone()[0]
            economic = edg(conn)
            state = self.store.paper_state(*self.store.load_paper(self.account_id))
            journal_max = conn.execute("SELECT COALESCE(MAX(id),0) FROM journal").fetchone()[0]
            events = None
            if journal_after is not None:
                events = [{"journal_id": row[0], "event_type": row[1], "entity_id": row[2], "run_id": row[3],
                           "symbol": row[4]}
                          for row in conn.execute("SELECT id,event_type,source,run_id,symbol FROM journal "
                                                  "WHERE id>? AND id<=? ORDER BY id", (journal_after, journal_max))]
            total_changes = conn.total_changes
        return {"data_version": data_version, "total_changes": total_changes, "edg": economic["edg"],
                "edg_tables": economic["tables"], "psh": psh(state), "state": [list(part) if isinstance(part, tuple)
                                                                               else part for part in state],
                "journal_max_id": journal_max, "events": events}

    def before_write(self, expected_state):
        """Pre-write evidence; returns a token for ``after_write`` (never raises)."""
        token = {"expected_psh": None, "pre": None, "pre_failed": None}
        try:
            token["expected_psh"] = None if expected_state is None else psh(expected_state)
        except Exception as exc:  # noqa: BLE001
            token["pre_failed"] = _error_name(exc)
            self.fail("write:expected_psh", exc)
        try:
            token["pre"] = self._snapshot()
        except Exception as exc:  # noqa: BLE001
            token["pre_failed"] = _error_name(exc)
            self.fail("write:pre_snapshot", exc)
        return token

    def after_write(self, token, broker, result, error=None, total_changes_after_save=None):
        """Post-write evidence and the REX_WRITE row (never raises)."""
        if token is None:  # the pre-write evidence could not even be started
            token = {"expected_psh": None, "pre": None, "pre_failed": "before_write_failed"}
        try:
            self._after_write(token, broker, result, error, total_changes_after_save)
        except Exception as exc:  # noqa: BLE001
            self.fail("write:record", exc)

    def _after_write(self, token, broker, result, error, total_changes_after_save):
        self.write_seq += 1
        self._attempt += 1
        outcome = "RAISED" if error is not None else "STALE" if result is False else "COMMITTED"
        entry = {"rex_version": REX_VERSION, "kind": "WRITE", "run_id": self.run_id, "slot_key": self.slot_key,
                 "symbol": self.symbol, "write_seq": self.write_seq, "attempt": self._attempt,
                 "context": _Encoded(self._context), "result": outcome, "error_type": None if error is None else error,
                 "save_paper_returned": None if error is not None else ("False" if result is False else repr(result)),
                 "expected_psh": token["expected_psh"], "failures": []}
        pre = token["pre"]
        if token["pre_failed"] is not None:
            entry["failures"].append({"where": "pre", "error_type": token["pre_failed"]})
        post = None
        try:
            post = self._snapshot(journal_after=None if pre is None else pre["journal_max_id"])
        except Exception as exc:  # noqa: BLE001
            entry["failures"].append({"where": "post", "error_type": _error_name(exc)})
            self.fail("write:post_snapshot", exc)
        saved_psh = None
        if outcome == "COMMITTED":
            try:
                saved_psh = psh(self.store.paper_state(broker.account, broker.orders, broker.fills))
            except Exception as exc:  # noqa: BLE001
                entry["failures"].append({"where": "saved_state", "error_type": _error_name(exc)})
        entry["pre"] = None if pre is None else {k: pre[k] for k in ("data_version", "total_changes", "edg",
                                                                     "edg_tables", "psh", "journal_max_id")}
        entry["pre_state"] = None if pre is None else pre["state"]
        entry["post"] = None if post is None else {k: post[k] for k in ("data_version", "total_changes", "edg",
                                                                        "edg_tables", "psh", "journal_max_id")}
        entry["post_state"] = None if post is None else post["state"]
        entry["journal_events"] = None if post is None else post["events"]
        entry["edg_before"] = None if pre is None else pre["edg"]
        entry["psh_before"] = None if pre is None else pre["psh"]
        entry["edg_after"] = post["edg"] if post is not None and outcome == "COMMITTED" else None
        entry["psh_after"] = post["psh"] if post is not None and outcome == "COMMITTED" else None
        integrity = {}
        if pre is not None and post is not None:
            integrity["foreign_write_detected"] = pre["data_version"] != post["data_version"]
            integrity["same_connection_write_detected"] = (total_changes_after_save is None
                                                           or post["total_changes"] != total_changes_after_save)
            if outcome != "COMMITTED":
                integrity["unexpected_change"] = (post["edg"] != pre["edg"]
                                                  or total_changes_after_save != pre["total_changes"])
        if outcome == "COMMITTED":
            integrity["expected_matches_pre"] = pre is not None and token["expected_psh"] == pre["psh"]
            integrity["saved_matches_post"] = post is not None and saved_psh == post["psh"]
        entry["integrity"] = integrity
        entry["complete"] = (pre is not None and post is not None and not entry["failures"]
                             and not self._context.get("context_failed")
                             and not integrity.get("foreign_write_detected")
                             and not integrity.get("same_connection_write_detected")
                             and not integrity.get("unexpected_change")
                             and integrity.get("expected_matches_pre", True)
                             and integrity.get("saved_matches_post", True))
        journal_id = self._write_row(REX_WRITE, entry)
        self.writes.append({"write_seq": entry["write_seq"], "rex_journal_id": journal_id, "result": outcome,
                            "stage": self._context.get("stage"), "attempt": entry["attempt"],
                            "complete": entry["complete"]})

    def _write_row(self, kind, record):
        payload = rex_payload(record)
        with self.store.transaction():
            self.store._event(self.clock(), self.run_id, self.symbol, REX_SOURCE, kind, "INFO", payload)
            return self.store.db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # -- end of run --------------------------------------------------------------------------------------------------
    def finish(self, returned):
        """Write the REX_RUN row (once). Never raises."""
        if self.finished:
            return
        self.finished = True
        try:
            record = {"rex_version": REX_VERSION, "kind": "RUN", "run_id": self.run_id, "slot_key": self.slot_key,
                      "symbol": self.symbol, "slot": self.slot, "identity": self.identity, "stages": self.stages,
                      "ai_requests": self.ai_requests, "ai_observations": self.ai_observations,
                      "writes": self.writes, "returned": returned,
                      "failures": list(self.failures)}
            record["complete"] = (not self.failures and all(w["complete"] for w in self.writes)
                                  and all(w["rex_journal_id"] is not None for w in self.writes))
            self._write_row(REX_RUN, record)
        except Exception as exc:  # noqa: BLE001
            self.fail("run:record", exc)


class RexStore:
    """``Store`` proxy for one run: ``save_paper`` is forwarded unchanged (same arguments, same result, same exception)
    and surrounded by REX evidence; every other attribute is the real store's."""

    def __init__(self, store, recorder):
        self._store = store
        self._recorder = recorder

    def __getattr__(self, name):
        return getattr(self._store, name)

    def save_paper(self, broker, *, owner_key=None, symbol=None, expected_state=None):
        token = _isolated(lambda: self._recorder.before_write(expected_state))
        try:
            result = self._store.save_paper(broker, owner_key=owner_key, symbol=symbol, expected_state=expected_state)
        except BaseException as exc:
            changes = _total_changes(self._store)
            _isolated(lambda: self._recorder.after_write(token, broker, None, error=_error_name(exc),
                                                         total_changes_after_save=changes))
            raise
        changes = _total_changes(self._store)
        _isolated(lambda: self._recorder.after_write(token, broker, result, total_changes_after_save=changes))
        return result


def _isolated(action):
    """Defence in depth around every recorder call made from the economic path: a REX defect never propagates."""
    try:
        return action()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("component=rex event=REX_ISOLATED_FAILURE error_type=%s", _error_name(exc))
        return None


def _total_changes(store):
    try:
        return store.db.total_changes
    except Exception:  # noqa: BLE001
        return None


class RexProviderProbe:
    """AI provider proxy: records the identity of each request handed to the provider, then delegates unchanged.
    Requests the runtime never hands to a provider (no usable evidence) are not observed here (stated in the REX)."""

    def __init__(self, provider, recorder):
        self._provider = provider
        self._recorder = recorder

    def __getattr__(self, name):
        return getattr(self._provider, name)

    def generate(self, request):
        _isolated(lambda: self._recorder.ai_request(request))
        return self._provider.generate(request)


def code_sha():
    value = os.environ.get("AI_FLOOR_GIT_COMMIT")
    return value if isinstance(value, str) else None


def decode_rex_text(text):
    """Parse a REX row's ``rex`` text (for readers); values stay in their typed form."""
    return json.loads(text)


__all__ = ["ECONOMIC_EVENTS", "REX_FAILURE", "REX_MAX_BYTES", "REX_RUN", "REX_SOURCE", "REX_VERSION", "REX_WRITE",
           "RexError", "RexProviderProbe", "RexRecorder", "RexStore", "code_sha", "decode_rex_text", "rex_encode",
           "rex_payload", "rule_identity"]
