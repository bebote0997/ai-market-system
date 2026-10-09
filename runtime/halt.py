"""V2 Phase 8 / P4a R-HALT (design P8.6 3.8; DEC-8.17b Alternative 1, admission contract; Owner decision D-1).

Behind ``RuntimeConfig.v2_rhalt`` (``AI_FLOOR_V2_RHALT``, OFF by default; ON requires ``v2_rex``). Implementation is
not activation: nothing here is enabled operationally.

Instants (never interchanged):
- ``T_h``: the signal handler's single assignment ``gate.requested_at = (monotonic_ns, utc, signum)``. The handler
  does no I/O, takes no lock, opens no transaction and raises nothing (I-R11).
- ``L(W)`` (read 1): ``gate.admit(kind)`` immediately before an economic write attempt loads its state. Its timestamp
  is taken BEFORE the read, so an admitted write always has ``l_w_ns < T_h``.
- ``read2``: ``gate.pre_save(admission)`` immediately before invoking ``save_paper``; refused -> no call, no
  transaction, no effect. Timestamp taken before the read (``read2_ns < T_h`` for every admitted call).
- ``T_stop``: ``save_paper`` returned (recorded by the REX store).
- ``T_ack``: the halt bookkeeping transaction (H).

Guarantee (accurate wording, DEC-8.17b): after ``T_h`` no economic write is admitted (read 1) or calls ``save_paper``
(read 2). At most ONE write whose read 2 preceded ``T_h`` may begin its transaction and commit after ``T_h`` (the
accepted residual, measured and reported, never hidden). Every STALE retry is a new attempt with its own admission.

Post-halt persistence (I-R1c, I-R14): every persistence site of the cycle, scheduler and close path is classified in
``PERSISTENCE_SITES`` and guarded by ``gate.allow(category)``. After ``T_h`` only H (the bookkeeping transaction),
P (``heartbeat``, ``scheduler``, ``runner`` keys) and R (REX evidence rows of admitted writes and of the halted run,
required by DEC-8.17b condition (b)) are allowed; D, O, S are suppressed; L (claim, run metadata, finish) is refused
and ``finish`` is replaced by the bookkeeping.

D-1 (Owner, 2026-10-09): the H transaction also deletes the ``symbol_locks`` row of the halted run ONLY (matched by
``slot_key`` and ``symbol``), in the same transaction; a documented exception to the original I-R1c allowlist.
If the transaction fails, nothing is committed and the halt is NOT confirmed (``HALT_UNCONFIRMED``): the run stays
RUNNING and is recovered only by the next authorized start.

Persistent halt and no automatic resume: the committed ``HALT_OBSERVED`` row is the persistent state.
``startup_halt_check`` (read-only, before any write, including ``recover()``) refuses to start while the latest
``HALT_OBSERVED`` journal id is not exactly the value of ``AI_FLOOR_V2_RHALT_RESUME``. A resume writes nothing (I-R1d);
a new halt has a new id, so an old token never resumes it.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
import uuid

LOG = logging.getLogger("ai_floor.halt")
RESUME_ENV = "AI_FLOOR_V2_RHALT_RESUME"
HALT_SOURCE = "halt"
HALT_EVENT = "HALT_OBSERVED"
HALT_VERSION = "V2_P4A_RHALT/1"
ALLOWED_AFTER_HALT = frozenset({"H", "P", "R"})
CATEGORIES = {"E": "economic", "D": "decision records", "O": "observability", "L": "run lifecycle",
              "H": "halt bookkeeping", "P": "process shutdown keys", "S": "summaries and notifications",
              "R": "REX evidence", "READ": "read only"}
# I-R14: every Store method reached from the cycle, the scheduler and the close path, with its category.
PERSISTENCE_SITES = {
    "save_paper": "E", "claim_slot": "L", "save_run_metadata": "L", "finish": "L",
    "save_reports": "D", "record_execution": "D", "record_analysis_events": "D", "record_macro_awareness": "D",
    "event": "O", "_event": "O", "set_state": "O", "save_snapshot": "O", "transaction": "O",
    "capture_notifications": "S", "claim_notification_delivery": "S", "complete_notification_delivery": "S",
    "heartbeat": "P", "close": "P",
    "load_paper": "READ", "paper_state": "READ", "owns_slot": "READ", "get_state": "READ", "run": "READ",
    "review_report": "READ", "journal": "READ", "db": "READ",
}


class HaltRefused(Exception):
    """Raised at a refused admission (economic or run lifecycle) after ``T_h``. Carries what was refused."""

    def __init__(self, kind, stage="admission"):
        super().__init__(f"halt: {kind} refused at {stage}")
        self.kind, self.stage = kind, stage


class HaltPending(RuntimeError):
    """Startup refused: an unresolved HALT exists (or the startup check could not be completed)."""


@dataclass
class Admission:
    process_id: str
    seq: int
    kind: str
    l_w_ns: int
    l_w_utc: str
    read2_ns: int = None
    read2_utc: str = None
    t_stop_ns: int = None

    def evidence(self):
        return asdict(self)


def _utc_now():
    return datetime.now(timezone.utc)


class HaltGate:
    """One per process. All reads and the handler's store run on the main thread (CPython)."""

    def __init__(self):
        self.process_id = str(uuid.uuid4())
        self.requested_at = None  # (monotonic_ns, utc datetime, signum) once T_h happened
        self.seq = 0
        self.admissions = []
        self.current = None
        self.refused = []
        self.recorded_journal_id = None  # HALT_OBSERVED of this process, once committed

    # -- the signal handler: one assignment ---------------------------------------------------------------------------
    def request(self, signum=None, frame=None):  # noqa: ARG002 - signal handler signature
        if self.requested_at is None:
            self.requested_at = (time.monotonic_ns(), _utc_now(), signum)

    @property
    def halted(self):
        return self.requested_at is not None

    # -- admission (Alternative 1) ------------------------------------------------------------------------------------
    def admit(self, kind):
        """Read 1 = L(W). Returns the Admission, or raises HaltRefused."""
        now_ns, now = time.monotonic_ns(), _utc_now()  # taken BEFORE the read
        if self.requested_at is not None:
            self.refused.append({"kind": kind, "stage": "admission"})
            raise HaltRefused(kind, "admission")
        self.seq += 1
        admission = Admission(self.process_id, self.seq, kind, now_ns, now.isoformat())
        self.admissions.append(admission)
        self.current = admission
        return admission

    def pre_save(self, admission):
        """Read 2, immediately before invoking ``save_paper``. Raises HaltRefused (no call, no effect)."""
        now_ns, now = time.monotonic_ns(), _utc_now()
        if self.requested_at is not None:
            self.refused.append({"kind": admission.kind, "stage": "read2", "seq": admission.seq})
            raise HaltRefused(admission.kind, "read2")
        admission.read2_ns, admission.read2_utc = now_ns, now.isoformat()
        self.current = admission

    def saved(self, admission, t_stop_ns):
        admission.t_stop_ns = t_stop_ns

    def allow(self, category):
        """Non-economic persistence: everything before T_h; after it only H, P and R."""
        if category not in CATEGORIES:
            raise ValueError(f"unclassified persistence category: {category}")
        return self.requested_at is None or category in ALLOWED_AFTER_HALT

    # -- evidence -----------------------------------------------------------------------------------------------------
    def residual(self):
        """Admitted writes whose save returned after T_h (DEC-8.17b residual), with measured deltas."""
        if self.requested_at is None:
            return []
        t_h = self.requested_at[0]
        return [{"seq": a.seq, "kind": a.kind, "read2_to_t_h_ns": t_h - a.read2_ns, "t_h_to_t_stop_ns": a.t_stop_ns - t_h}
                for a in self.admissions
                if a.read2_ns is not None and a.t_stop_ns is not None and a.t_stop_ns > t_h]

    def halt_payload(self, *, refused_kind=None, refused_stage=None, run_id=None, slot_key=None, symbol=None):
        t_h_ns, t_h_utc, signum = self.requested_at
        last = self.admissions[-1] if self.admissions else None
        return {"halt_version": HALT_VERSION, "process_id": self.process_id, "t_h_monotonic_ns": t_h_ns,
                "t_h_utc": t_h_utc.isoformat(), "signal": signum, "refused_kind": refused_kind,
                "refused_stage": refused_stage, "run_id": run_id, "slot_key": slot_key, "symbol": symbol,
                "admissions_total": len(self.admissions),
                "last_admitted": None if last is None else last.evidence(), "residual": self.residual(),
                "refused": list(self.refused)}


def halt_bookkeeping(store, gate, *, run_id=None, slot_key=None, symbol=None, refused_kind=None, refused_stage=None):
    """The H transaction (one ``BEGIN IMMEDIATE``): the halted run's status fields, the release of THAT run's symbol
    lock (D-1) and one HALT_OBSERVED row; or, with no run in flight, the HALT_OBSERVED row alone. Returns the journal
    id. Any failure rolls the whole transaction back and propagates (the halt is then NOT confirmed)."""
    payload = gate.halt_payload(refused_kind=refused_kind, refused_stage=refused_stage, run_id=run_id,
                                slot_key=slot_key, symbol=symbol)
    with store.transaction():
        if slot_key is not None:
            row = store.db.execute("SELECT run_id, symbol, status FROM runs WHERE slot_key=?", (slot_key,)).fetchone()
            if row is None or row[0] != run_id or row[1] != symbol or row[2] != "RUNNING":
                raise RuntimeError("halt bookkeeping: the halted run is not RUNNING")
            payload["t_ack_ns"] = time.monotonic_ns()  # inside the transaction, after every admitted write returned
            store.db.execute("UPDATE runs SET status='COMPLETED', final_status='HALTED', completed_at=? "
                             "WHERE slot_key=? AND status='RUNNING'", (_utc_now().isoformat(), slot_key))
            released = store.db.execute("DELETE FROM symbol_locks WHERE slot_key=? AND symbol=?",
                                        (slot_key, symbol)).rowcount
            payload["symbol_lock_released"] = released == 1
        else:
            payload["t_ack_ns"] = time.monotonic_ns()
            payload["symbol_lock_released"] = None
        store._event(_utc_now(), run_id, symbol, HALT_SOURCE, HALT_EVENT, "WARNING", payload)
        journal_id = store.db.execute("SELECT last_insert_rowid()").fetchone()[0]
    gate.recorded_journal_id = journal_id
    return journal_id


def latest_halt(conn):
    row = conn.execute("SELECT id, payload FROM journal WHERE source=? AND event_type=? ORDER BY id DESC LIMIT 1",
                       (HALT_SOURCE, HALT_EVENT)).fetchone()
    return None if row is None else (row[0], row[1])


def startup_halt_check(config, env):
    """Read-only startup barrier (flag ON only), before ANY write. Raises HaltPending while the latest HALT_OBSERVED
    is unresolved, i.e. ``env[AI_FLOOR_V2_RHALT_RESUME]`` is not exactly its journal id. Any failure to complete the
    check refuses the start (fail closed). Writes nothing, ever."""
    if not getattr(config, "v2_rhalt", False):
        return None
    path = Path(config.db_path)
    if not path.exists():
        return None  # nothing can have been halted in a database that does not exist
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal'").fetchone()
            halt = latest_halt(conn) if exists else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - an incomplete check is a refusal
        raise HaltPending(f"startup halt check failed: {type(exc).__name__}") from None
    if halt is None:
        return None
    token = (env or {}).get(RESUME_ENV)
    if token != str(halt[0]):
        raise HaltPending(f"unresolved HALT_OBSERVED journal id {halt[0]}: start refused (no automatic resume)")
    LOG.warning("component=halt event=RESUME_TOKEN_ACCEPTED halt_journal_id=%s", halt[0])
    return halt[0]


def decode_payload(text):
    return json.loads(text)


__all__ = ["ALLOWED_AFTER_HALT", "Admission", "CATEGORIES", "HALT_EVENT", "HALT_SOURCE", "HaltGate", "HaltPending",
           "HaltRefused", "PERSISTENCE_SITES", "RESUME_ENV", "halt_bookkeeping", "latest_halt",
           "startup_halt_check"]
