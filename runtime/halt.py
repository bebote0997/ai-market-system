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
P (``heartbeat``, ``scheduler``, ``runner`` keys) and R are allowed; D, O, S are suppressed; L (claim, run metadata,
finish) is refused and ``finish`` is replaced by the bookkeeping. STARTUP writes (schema creation, ``recover()``, the
startup ``system_state`` keys, the preflight write probe, experiment initialization, notification capture) are refused
after ``T_h`` by ``startup_write``: the constructor stops without writing (MEDIUM-1). A halt requested during a
startup check + write is deferred to the end of that section (the handler still only assigns).

R (Owner ratification, CONDITIONED; DEC-8.17b condition (b)): after ``T_h`` only REX_WRITE evidence of an admitted
write of THIS process whose read 2 preceded ``T_h`` (once per admission) and one REX_RUN of the halted run (the run in
flight at ``T_h``); never REX_FAILURE, another run, another process, a new run or a duplicate
(``HaltGate.allow_evidence``). R never authorizes an economic write.

D-1 (Owner, 2026-10-09): the H transaction also deletes the ``symbol_locks`` row of the halted run ONLY (matched by
``slot_key`` and ``symbol``), in the same transaction; a documented exception to the original I-R1c allowlist.
If the transaction fails, nothing is committed and the halt is NOT confirmed (``HALT_UNCONFIRMED``): the run stays
RUNNING and is recovered only by the next authorized start.

Persistent halt and no automatic resume: the committed ``HALT_OBSERVED`` row is the persistent state.
``startup_halt_check`` (read-only, before any write, including ``recover()``) refuses to start while the latest
``HALT_OBSERVED`` journal id is not exactly the value of ``AI_FLOOR_V2_RHALT_RESUME``. A resume writes nothing (I-R1d);
a new halt has a new id, so an old token never resumes it.
"""
from contextlib import contextmanager
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
              "R": "REX evidence of the halted run only (validated by allow_evidence)",
              "STARTUP": "process start (schema, recover, startup state, preflight, experiment, notifications)",
              "READ": "read only"}
# R after T_h (Owner ratification, conditioned): only these REX event types, each validated in context.
R_EVENTS_AFTER_HALT = frozenset({"REX_WRITE", "REX_RUN"})

# I-R14: every Store method reached from the cycle, the scheduler and the close path, with its category.
PERSISTENCE_SITES = {
    "save_paper": "E", "claim_slot": "L", "save_run_metadata": "L", "finish": "L",
    "save_reports": "D", "record_execution": "D", "record_analysis_events": "D", "record_macro_awareness": "D",
    "event": "O", "_event": "O", "set_state": "O", "save_snapshot": "O", "transaction": "O",
    "capture_notifications": "S", "claim_notification_delivery": "S", "complete_notification_delivery": "S",
    "heartbeat": "P", "close": "P",
    # MEDIUM-1: every startup write (constructor paths), refused after T_h by ``startup_write``
    "Store": "STARTUP", "recover": "STARTUP", "start_experiment_if_unstarted": "STARTUP", "preflight": "STARTUP",
    "cloud_preflight": "STARTUP",
    # R: REX journal rows, validated by ``HaltGate.allow_evidence`` after T_h
    "rex_write_row": "R",
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
        self.active_run = None  # the run in flight (set after its claim, cleared when it returns)
        self.startup_depth = 0  # > 0 inside a startup check + write section (MEDIUM-1)
        self.deferred_signal = None  # a halt requested inside such a section, applied when the section ends
        self._evidence_writes, self._evidence_runs = set(), set()
        self._last_ns = 0

    def now_ns(self):
        """The gate clock: ``time.monotonic_ns()`` made strictly increasing per process. Every halt instant (L(W),
        read2, T_h, T_stop, T_ack) comes from it, so their order is never lost to clock resolution (a coarse clock,
        e.g. ~15.6 ms on Windows, returned equal values: read2 == T_h dropped the residual's REX_WRITE and T_stop ==
        T_h hid the residual).

        Main thread only; reentrancy-safe against the handler, which may run at any bytecode of this method. The
        handler never writes ``_last_ns``: it only reads it (``T_h = max(monotonic, _last_ns + 1)``, see ``request``),
        so T_h is above every value already returned. The value is stored BEFORE ``requested_at`` is read: a handler
        that ran before the store is seen by the read and the value is moved above T_h; one that ran after the store
        read the stored value and set T_h above it. So every value is != T_h, every value returned after T_h is set
        is > T_h, and an instant overlapping T_h is ordered after it (conservative: a residual is over-reported, never
        hidden; an admission or read 2 overlapping it is refused, as it checks ``requested_at`` afterwards)."""
        now = max(time.monotonic_ns(), self._last_ns + 1)
        self._last_ns = now
        requested = self.requested_at
        if requested is not None and now <= requested[0]:
            now = requested[0] + 1
            self._last_ns = now
        return now

    # -- the signal handler: one assignment ---------------------------------------------------------------------------
    def request(self, signum=None, frame=None):  # noqa: ARG002 - signal handler signature
        if self.requested_at is None:
            if self.startup_depth:  # inside a startup check + write: T_h is set when that write has completed
                self.deferred_signal = 0 if signum is None else signum
            else:  # reads the gate clock, never writes it (it may have interrupted ``now_ns``)
                self.requested_at = (max(time.monotonic_ns(), self._last_ns + 1), _utc_now(), signum)

    @property
    def halted(self):
        return self.requested_at is not None

    # -- admission (Alternative 1) ------------------------------------------------------------------------------------
    def admit(self, kind):
        """Read 1 = L(W). Returns the Admission, or raises HaltRefused."""
        now_ns, now = self.now_ns(), _utc_now()  # taken BEFORE the read
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
        now_ns, now = self.now_ns(), _utc_now()
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

    def allow_evidence(self, kind, *, process_id, run_id, admission=None):
        """Category R after T_h (restricted): REX evidence of the halted run of THIS process only. REX_WRITE only for an
        admitted write whose read 2 preceded T_h, once per admission; REX_RUN once for the halted run. Anything else
        (another run or process, a new run, an unadmitted write, a duplicate, REX_FAILURE) is refused."""
        if self.requested_at is None:
            return True
        if kind not in R_EVENTS_AFTER_HALT or process_id != self.process_id:
            return False
        if run_id is None or run_id != self.active_run:
            return False
        if kind == "REX_WRITE":
            if (admission is None or admission.process_id != self.process_id or admission.read2_ns is None
                    or admission.read2_ns >= self.requested_at[0] or admission.seq in self._evidence_writes):
                return False
            self._evidence_writes.add(admission.seq)
            return True
        if run_id in self._evidence_runs:
            return False
        self._evidence_runs.add(run_id)
        return True

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


@contextmanager
def startup_write(gate, site):
    """MEDIUM-1: one startup persistence site. Refused after T_h (nothing written, HaltRefused). A halt requested while
    the check + write is in progress (the signal handler runs between bytecodes, possibly mid-write) only records a
    deferred request; T_h is set when the section ends, right after the write, so no startup write ever follows T_h
    and the next site is refused. Independent of the OS and of threads. Without a gate (flag OFF) a plain no-op."""
    if gate is None:
        yield
        return
    gate.startup_depth += 1
    try:
        if gate.requested_at is not None:
            gate.refused.append({"kind": "startup", "stage": site})
            raise HaltRefused("startup", site)
        yield
    finally:
        gate.startup_depth -= 1
        if gate.startup_depth == 0 and gate.deferred_signal is not None:
            signum, gate.deferred_signal = gate.deferred_signal, None
            gate.request(signum or None)  # T_h: now, after the section's write


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
            payload["t_ack_ns"] = gate.now_ns()  # inside the transaction, after every admitted write returned
            store.db.execute("UPDATE runs SET status='COMPLETED', final_status='HALTED', completed_at=? "
                             "WHERE slot_key=? AND status='RUNNING'", (_utc_now().isoformat(), slot_key))
            released = store.db.execute("DELETE FROM symbol_locks WHERE slot_key=? AND symbol=?",
                                        (slot_key, symbol)).rowcount
            payload["symbol_lock_released"] = released == 1
        else:
            payload["t_ack_ns"] = gate.now_ns()
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


__all__ = ["ALLOWED_AFTER_HALT", "Admission", "CATEGORIES", "R_EVENTS_AFTER_HALT", "startup_write", "HALT_EVENT", "HALT_SOURCE", "HaltGate", "HaltPending",
           "HaltRefused", "PERSISTENCE_SITES", "RESUME_ENV", "halt_bookkeeping", "latest_halt",
           "startup_halt_check"]
