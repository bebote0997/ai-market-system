"""One durable PAPER cycle. Explicit dependencies; no network or sample fallback."""
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import json
import sqlite3
import time
import uuid

from ai.orchestrator import run as run_ai
from ai.provider import DeterministicAIProvider
from ai.runtime import AuditLog
from data.macro_news import InMemoryMacroNewsProvider, NoMacroDataProvider
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.pending_order_gate import (BLOCKING_FINAL_STATUSES, CurrentCycleGate, PendingGateEvidenceError,
                                          gate_pending_orders)
from execution.position_catch_up import CatchUpEvidenceError, catch_up_position
from execution.trade_manager import TradeManager
from floor.orchestrator import run as run_floor
from riesgo import crear_configuracion_riesgo_v2
from runtime.config import RuntimeConfig
from runtime import health_hooks
from runtime.gates import fresh_snapshot, paper_policy
from runtime.observability import setup_id as audit_setup_id
from runtime.paper_contracts import apply_paper_quantity_increment
from runtime.scheduler import session_names, slot_at, slot_key
from storage.codec import utc
from storage.database import Store
from ui.adapters import MARKETS, from_ai_report

LOG = logging.getLogger("ai_floor.runtime")
STALE_PAPER_STATE = "STALE_PAPER_STATE"  # B2.3A: submission refused, PAPER state changed under it.
EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"  # B2.3B: PAPER economics fail closed for this cycle.
# V2 Phase 8 / P1-B (DEC-8.14, design 1.5): observe-only evidence ingestion for enabled symbols OUTSIDE the catch-up
# scope. Never blocks or changes the cycle; bounded lock wait; DEGRADED health after K consecutive failures.
EVIDENCE_OBSERVE_FAILED = "EVIDENCE_OBSERVE_FAILED"
EVIDENCE_OBSERVE_SKIPPED_BUSY = "EVIDENCE_OBSERVE_SKIPPED_BUSY"
OBSERVE_BUSY_TIMEOUT_MS = 500
EVIDENCE_BUSY_TIMEOUT_MS = 10000  # the Evidence Store's own default (restored after every observation)
OBSERVE_DEGRADED_AFTER = 4


class OperationalRuntime:
    def _audit_safely(self, action, run_id, symbol):
        try:
            action()
        except Exception as exc:
            LOG.warning("run_id=%s symbol=%s component=observability event=RECORD_FAILED error_type=%s",
                        run_id, symbol, type(exc).__name__)

    def _record_execution_safely(self, at, run_id, symbol, assessment, **outcome):
        """Audit is best effort and must never change a PAPER decision."""
        self._audit_safely(lambda: self.store.record_execution(
            at, run_id, symbol, setup_id=audit_setup_id(assessment), **outcome), run_id, symbol)

    def __init__(self, config=None, *, market_provider=None, ai_provider=None, macro_provider=None,
                 instruments=None, clock=None, risk_config=None, paper_enabled=True,
                 diagnostic_outside_session=False, recovery_stale_after_seconds=120, ai_pricing=None,
                 ai_soft_budget=None, ai_cycle_budget_seconds=120.0, ai_alerts=None):
        self.config = config or RuntimeConfig.from_env()
        self.paper_enabled = bool(paper_enabled)
        self.diagnostic_outside_session = bool(diagnostic_outside_session) and not self.paper_enabled
        if market_provider is None and self.config.market_provider_mode == "massive":
            from data.massive_provider import MassiveMarketDataProvider
            market_provider = MassiveMarketDataProvider(timeout=float(os.environ.get("MASSIVE_TIMEOUT_SECONDS", "15")))
        if market_provider is None and self.config.market_provider_mode == "twelve_data":
            from data.twelve_data_provider import TwelveDataMarketDataProvider
            market_provider = TwelveDataMarketDataProvider(timeout=float(os.environ.get("TWELVE_DATA_TIMEOUT_SECONDS", "15")))
        if ai_provider is None and self.config.ai_provider_mode == "openai":
            from ai.openai_provider import OpenAIProvider
            ai_provider = OpenAIProvider(timeout=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "30")))
        self.market_provider = market_provider
        self.ai_provider = ai_provider or DeterministicAIProvider()
        if macro_provider is None and self.config.macro_provider_mode == "finnhub":
            from data.finnhub_macro_provider import FinnhubMacroDataProvider
            macro_provider = FinnhubMacroDataProvider()
        if macro_provider is None and self.config.macro_provider_mode == "official_hybrid":
            from data.official_macro_provider import OfficialMacroProvider
            macro_provider = OfficialMacroProvider()
        if macro_provider is None and self.config.macro_provider_mode == "fxmacrodata":
            from data.fxmacrodata_provider import FXMacroDataProvider
            macro_provider = FXMacroDataProvider()
        self.macro_provider = macro_provider or (NoMacroDataProvider() if self.config.macro_provider_mode == "none"
                                                 else InMemoryMacroNewsProvider())
        self.instruments = instruments or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # V2 P7.1 Batch B: optional Owner price table / soft budget for AI_CALL rows (used only when the flag is ON).
        self.ai_pricing, self.ai_soft_budget = ai_pricing, ai_soft_budget
        # V2 P7.1 Batch C (used only when v2_ai_resilience is ON): per-cycle budget, in-process provider health
        # (cross-cycle, so recovery is observed without restart) and optional alerts (None = OFF).
        from ai.provider_health import ProviderHealthTracker
        self.ai_cycle_budget_seconds, self.ai_alerts = ai_cycle_budget_seconds, ai_alerts
        self.ai_health = ProviderHealthTracker()
        self.risk_config = risk_config or crear_configuracion_riesgo_v2()
        self.store = Store(self.config.db_path)
        self.evidence = None  # B2.3B: opened lazily, only when v2_position_catch_up is ON.
        self.store.recover(self.clock(), stale_after_seconds=recovery_stale_after_seconds)
        account, orders, fills = self.store.load_paper(self.config.account_id)
        if account is None:
            if orders or self.store.db.execute("SELECT 1 FROM paper_positions LIMIT 1").fetchone():
                self.store.event(self.clock(), None, None, "recovery", "STATE_INCONSISTENCY", "ERROR", {"reason": "paper_account_missing"})
                self.store.close()
                raise RuntimeError("paper state without account")
            account = PaperAccount("1.0", self.config.account_id, self.config.starting_equity,
                                   self.config.starting_equity, self.config.starting_equity)
            # B2.3A: create only if still absent; a concurrent creator's account is never overwritten.
            if self.store.save_paper(PaperBroker(account),
                                     expected_state=self.store.paper_state(None, orders, fills)) is False:
                account, orders, _ = self.store.load_paper(self.config.account_id)
                if account is None:
                    self.store.close()
                    raise RuntimeError("paper account creation raced without an account")
        for position in account.open_positions.values():
            origin = orders.get(position.origin_order_id)
            if origin is None or origin.status != "FILLED" or origin.symbol != position.symbol or origin.quantity != position.quantity:
                self.store.event(self.clock(), position.run_id, position.symbol, "recovery", "STATE_INCONSISTENCY", "ERROR", {"reason": "orphan_position"})
                self.store.close()
                raise RuntimeError("inconsistent paper position")
        with self.store.transaction():
            self.store.set_state("account_id", self.config.account_id)
            self.store.set_state("enabled_symbols", json.dumps(self.config.enabled_symbols))
            self.store.set_state("market_provider_mode", self.config.market_provider_mode)
            self.store.set_state("market_data_provider", "NOT CONFIGURED" if market_provider is None else "CONFIGURED")
            self.store.set_state("ai_provider", "DETERMINISTIC" if isinstance(self.ai_provider, DeterministicAIProvider) else "CONFIGURED")
            self.store.set_state("macro_provider", getattr(self.macro_provider, "health", "NOT CONFIGURED"))
            self.store.set_state("macro_provider_mode", self.config.macro_provider_mode)
        self.store.heartbeat(self.clock(), "STOPPED")

    def close(self):
        self.store.heartbeat(self.clock(), "STOPPED")
        self.store.close()
        if self.evidence is not None:
            self.evidence.store.close()

    def _broker(self, symbol):
        account, orders, fills = self.store.load_paper(self.config.account_id)
        broker = PaperBroker(account, self.instruments.get(symbol))
        broker.orders = orders
        broker.fills = fills
        return broker

    def _guarded_paper_write(self, symbol, key, apply, store=None):
        """B2.3A: run ``apply(broker)`` on freshly loaded durable PAPER state and persist it only if
        that state is still current (B2.1 ``expected_state``, revalidated under BEGIN IMMEDIATE).

        ``apply`` returns (save, result). On STALE the computation is discarded and redone once from
        a fresh load. Returns (broker, result), or (None, None) when both attempts were stale.
        ``store`` (V2 P3) is the run's REX store proxy, or None (legacy: ``self.store``).
        """
        writer = self.store if store is None else store
        for _ in range(2):
            broker = self._broker(symbol)
            expected_state = self.store.paper_state(broker.account, broker.orders, broker.fills)
            save, result = apply(broker)
            if not save or writer.save_paper(broker, owner_key=key, symbol=symbol,
                                             expected_state=expected_state) is not False:
                return broker, result
        return None, None

    def _evidence_engine(self):
        if self.evidence is None:
            from data.market_evidence import MarketEvidenceEngine
            from storage.evidence_store import EvidenceStore
            self.evidence = MarketEvidenceEngine(EvidenceStore(self.config.market_evidence_path),
                                                 enabled_symbols=self.config.enabled_symbols)
        return self.evidence

    def _ingest_evidence(self, symbol, snapshot, slot):
        """B2.3B: commit this snapshot's closed bars to the separate Evidence Store (closed by the
        slot, the provider's own boundary). Returns None, or the failure reason (fail closed)."""
        try:
            self._last_ingest = self._evidence_engine().ingest_snapshot(symbol, snapshot, as_of=slot)
            return None
        except Exception as exc:  # noqa: BLE001 - any evidence failure blocks PAPER economics
            LOG.warning("symbol=%s component=market_evidence event=%s error_type=%s",
                        symbol, EVIDENCE_UNAVAILABLE, type(exc).__name__)
            return EVIDENCE_UNAVAILABLE

    def _observe_evidence(self, symbol, snapshot, slot, run_id):
        """P1-B (DEC-8.14): observe-only ingestion for an enabled symbol OUTSIDE the catch-up scope. It never returns a
        blocking reason and never raises an Exception: the cycle continues on the legacy path with the same inputs as
        with the flag OFF. The lock wait is bounded (OBSERVE_BUSY_TIMEOUT_MS) and the store's own timeout is always
        restored; if it cannot be restored, the engine is closed so the next use reopens with the default."""
        started = time.monotonic()
        outcome, error_type = "OK", None
        try:
            engine = self._evidence_engine()
            engine.store.db.execute(f"PRAGMA busy_timeout={OBSERVE_BUSY_TIMEOUT_MS}")
            try:
                self._last_ingest = engine.ingest_snapshot(symbol, snapshot, as_of=slot)
            finally:
                try:
                    engine.store.db.execute(f"PRAGMA busy_timeout={EVIDENCE_BUSY_TIMEOUT_MS}")
                except Exception:  # noqa: BLE001 - never keep a shortened timeout for in-scope symbols
                    self._discard_evidence_engine()
        except sqlite3.OperationalError as exc:
            busy = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            outcome = EVIDENCE_OBSERVE_SKIPPED_BUSY if busy else EVIDENCE_OBSERVE_FAILED
            error_type = type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - observe-only: isolate every failure
            outcome, error_type = EVIDENCE_OBSERVE_FAILED, type(exc).__name__
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        if outcome != "OK":
            self._last_ingest = None
            LOG.warning("symbol=%s component=market_evidence event=%s error_type=%s", symbol, outcome, error_type)
        self._audit_safely(lambda: self._record_observe(symbol, run_id, outcome, error_type, elapsed_ms), run_id, symbol)

    def _discard_evidence_engine(self):
        engine, self.evidence = self.evidence, None
        try:
            if engine is not None:
                engine.store.close()
        except Exception:  # noqa: BLE001
            pass

    def _record_observe(self, symbol, run_id, outcome, error_type, elapsed_ms):
        """Non-economic: one WARNING event per failed observation and the per-symbol evidence_observe health state
        (DEGRADED after OBSERVE_DEGRADED_AFTER consecutive failures; a success resets it). Never changes trading."""
        with self.store.transaction():
            raw = self.store.get_state("evidence_observe")
            state = json.loads(raw) if raw else {}
            entry = state.get(symbol, {"consecutive_failures": 0, "state": "OK"})
            failures = 0 if outcome == "OK" else entry.get("consecutive_failures", 0) + 1
            state[symbol] = {"consecutive_failures": failures,
                             "state": "DEGRADED" if failures >= OBSERVE_DEGRADED_AFTER else "OK",
                             "last_outcome": outcome, "last_elapsed_ms": elapsed_ms}
            self.store.set_state("evidence_observe", json.dumps(state, sort_keys=True))
            if outcome != "OK":
                self.store._event(self.clock(), run_id, symbol, "market_evidence", outcome, "WARNING",
                                  {"error_type": error_type, "elapsed_ms": elapsed_ms,
                                   "consecutive_failures": failures})

    def _catch_up_positions(self, symbol, slot, key, store=None, observer=None):
        """B2.3B: every committed closed 5m bar after the open position's durable watermark, oldest
        first, through the accepted B2.1 catch-up (guarded save per bar). Never a newest-bar fallback.
        Returns None, or the reason PAPER economics are blocked for this cycle."""
        for _ in range(2):  # A STALE result resumes from the durable watermark once.
            try:
                result = catch_up_position(self.store if store is None else store, self.evidence,
                                           account_id=self.config.account_id,
                                           symbol=symbol, as_of=slot, instrument=self.instruments.get(symbol),
                                           owner_key=key, **({} if observer is None else {"observer": observer}))
            except CatchUpEvidenceError:
                return EVIDENCE_UNAVAILABLE
            if result.status != "STALE":
                return None
        return STALE_PAPER_STATE

    def _record_revisions(self, symbol, snapshot, run_id):
        from runtime import revision_review
        results = self._last_ingest or {}
        if not any(getattr(r, "revisions", ()) for r in results.values()):
            return
        committed = {(tf, bar.bar_start): {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
                     for tf, result in results.items() if result.revisions
                     for bar in self.evidence.committed(symbol, tf) if bar.bar_start in result.revisions}
        from data.market_evidence import bars_from_frame
        anomaly_ids = {}  # P8.4F: identity = the evidence database's REVISION anomaly for the presented content
        for tf, result in results.items():
            if result.revisions and tf in snapshot:
                for bar in bars_from_frame(snapshot[tf], symbol=symbol, timeframe=tf):
                    if bar.bar_start in result.revisions:
                        row = self.evidence.store.db.execute(
                            "SELECT anomaly_id FROM evidence_anomalies WHERE kind='REVISION' AND symbol=? AND "
                            "timeframe=? AND bar_start=? AND json_extract(payload,'$.presented_digest')=?",
                            (symbol, tf, bar.bar_start, bar.digest)).fetchone()
                        if row is not None:
                            anomaly_ids[(tf, bar.bar_start)] = row[0]
        account, _, _ = self.store.load_paper(self.config.account_id)
        positions = [] if account is None else [p for p in account.open_positions.values() if p.symbol == symbol]
        records = revision_review.classify(symbol, snapshot, results, committed, positions, anomaly_ids,
                                           in_scope=self.config.catch_up_applies(symbol))
        revision_review.journal(self.store, records, at=self.clock(), run_id=run_id)

    def _committed_bar(self, symbol, start):
        """B2.3C: the committed 5m Evidence bar starting at ``start`` (the current cycle's bar) in the
        dict form ``process_next_bar`` takes, or None. Committed evidence is authoritative (decision 3);
        there is never a snapshot fallback."""
        try:
            bars = self.evidence.committed(symbol, "5m", after=start - timedelta(microseconds=1))
        except Exception:  # noqa: BLE001 - unreadable evidence fails closed
            return None
        bar = next((b for b in bars if b.start == start), None)
        if bar is None or bar.is_closed is not True:
            return None
        return {"symbol": bar.symbol, "timestamp": bar.start, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "is_closed": True}

    def _gate_pending_orders(self, gate, symbol, slot, key, store=None, observer=None):
        """B2.3C: pending orders progress only through the accepted B2.2 ``gate_pending_orders`` (P1).
        A STALE result is retried once from fresh durable state. Returns None, or the reason PAPER
        economics are blocked for this cycle."""
        for _ in range(2):
            try:
                result = gate_pending_orders(self.store if store is None else store, self.evidence,
                                             account_id=self.config.account_id,
                                             symbol=symbol, as_of=slot, gate=gate,
                                             instrument=self.instruments.get(symbol), owner_key=key,
                                             **({} if observer is None else {"observer": observer}))
            except PendingGateEvidenceError:
                return EVIDENCE_UNAVAILABLE
            if result.status != "STALE":
                return None
        return STALE_PAPER_STATE

    def _record_ai_health(self, events, run_id, symbol):
        """Observability only: journal provider health transitions; alerts only if an alerts object was supplied."""
        for event in events:
            self.store.event(self.clock(), run_id, symbol, "ai_provider_health", "AI_" + event["event"],
                             "WARNING" if event["event"] != "PROVIDER_RECOVERED" else "INFO", event)
        if self.ai_alerts is not None:
            self.ai_alerts.process(events, symbol=symbol)

    def _record_ai_calls(self, audit, ai, deterministic, symbol, key):
        from ai.call_audit import build_records, evaluate_soft_budget, persist
        records = build_records(audit.entries, run_id=ai.run_id, symbol=symbol,
                                setup_id=audit_setup_id(deterministic.setup_assessment), pricing=self.ai_pricing)
        at = self.clock()
        persist(self.store, records, at=at, owner_key=key, symbol=symbol)
        evaluate_soft_budget(self.store, records, self.ai_soft_budget, at=at)

    def _snapshot(self, vm):
        plan = None if vm.plan is None else {k: getattr(vm.plan, k) for k in
            ("entry", "stop", "target", "risk_reward", "side", "invalidation")}
        risk = None if vm.risk_decision is None else {k: getattr(vm.risk_decision, k) for k in
            ("status", "reason", "equity_at_decision", "risk_fraction", "quantity", "capital_at_risk")}
        return {"schema_version": "1.0", "run_id": vm.run_id, "symbol": vm.symbol,
                "as_of": utc(vm.as_of) if vm.as_of else None, "state": vm.state,
                "setup_status": vm.setup_status, "setup_side": vm.setup_side,
                "setup_evidence": vm.setup_evidence, "setup_invalidation": vm.setup_invalidation,
                "risk_status": vm.risk_status, "plan": plan, "risk_decision": risk,
                "warnings": vm.warnings, "facts": vm.facts,
                "interpretation": vm.interpretation, "freshness": vm.freshness,
                "agents": [vars(a) for a in vm.agents], "prompt_versions": vm.prompt_versions}

    def _status_snapshot(self, symbol, slot, state, warning, run_id=None):
        return {"schema_version": "1.0", "run_id": run_id, "symbol": symbol, "as_of": utc(slot),
                "state": state, "setup_status": "NO_SETUP", "setup_side": None,
                "setup_evidence": [], "setup_invalidation": None, "risk_status": "NOT CALLED",
                "warnings": [warning], "facts": [f"{symbol}: {state}", "Risk Engine: NOT CALLED"],
                "interpretation": [], "freshness": state, "agents": [], "prompt_versions": []}

    # -- V2 P3 REX (flag v2_rex, OFF by default): evidence only; every helper is a no-op when ``rex`` is None ------
    def _new_rex(self, run_id, key, symbol, slot):
        if not self.config.v2_rex:
            return None
        try:
            from runtime.rex import RexRecorder, code_sha, rule_identity
            identity = {"rule_identity": rule_identity(
                            extra_files=("execution/pending_order_gate.py",),
                            extra_constants={"gate_blocking_final_statuses": sorted(BLOCKING_FINAL_STATUSES)}),
                        "code_sha": code_sha(), "config_fingerprint": self.config.fingerprint(),
                        "enabled_symbols": list(self.config.enabled_symbols), "sessions": list(self.config.sessions),
                        "cadence_minutes": self.config.cadence_minutes, "max_age_seconds": self.config.max_age_seconds,
                        "v2_position_catch_up": self.config.v2_position_catch_up,
                        "catch_up_scope": list(self.config.v2_position_catch_up_symbols),
                        "catch_up_applies": self.config.catch_up_applies(symbol),
                        "v2_ai_resilience": self.config.v2_ai_resilience, "paper_enabled": self.paper_enabled,
                        "diagnostic_outside_session": self.diagnostic_outside_session,
                        "broker_rr_policy": None, "risk_config": dict(self.risk_config or {}),
                        "instrument": self.instruments.get(symbol), "account_id": self.config.account_id}
            return RexRecorder(self.store, account_id=self.config.account_id, run_id=run_id, slot_key=key,
                               symbol=symbol, slot=slot, identity=identity)
        except Exception as exc:  # noqa: BLE001 - no REX for this run (the chain verifier reports it uncovered)
            LOG.warning("run_id=%s symbol=%s component=rex event=REX_UNAVAILABLE error_type=%s",
                        run_id, symbol, type(exc).__name__)
            return None

    @staticmethod
    def _rex_note(rex, stage, build):
        """Record stage evidence built by ``build()``; never raises and never runs when REX is OFF."""
        if rex is None:
            return
        try:
            rex.stage(stage, build())
        except Exception as exc:  # noqa: BLE001 - defence in depth: REX never changes the cycle
            try:
                rex.fail(f"stage:{stage}", exc)
            except Exception:  # noqa: BLE001
                LOG.warning("component=rex event=REX_FAILURE_NOT_RECORDED stage=%s", stage)

    @staticmethod
    def _rex_context(rex, stage, build):
        if rex is None:
            return
        try:
            rex.context(stage, build())
        except Exception as exc:  # noqa: BLE001 - defence in depth: REX never changes the cycle
            try:
                rex.fail(f"context:{stage}", exc)
                rex.context(stage, {"context_build_failed": True})
            except Exception:  # noqa: BLE001
                LOG.warning("component=rex event=REX_FAILURE_NOT_RECORDED stage=%s", stage)

    def _rex_floor(self, report, symbol, equity):
        setup = report.setup_assessment
        return {"equity": equity, "final_status": report.final_status, "warnings": list(report.warnings),
                "setup": None if setup is None else {"status": setup.status, "side": setup.side,
                                                     "warnings": list(setup.warnings)},
                "trade_plan": report.trade_plan, "risk_decision": report.risk_decision,
                "target_decision": report.target_decision, "risk_config": dict(self.risk_config or {}),
                "instrument": self.instruments.get(symbol)}

    @staticmethod
    def _rex_ai(ai, audit):
        def identity(response):
            return None if response is None else {
                "schema_version": response.schema_version, "run_id": response.run_id, "symbol": response.symbol,
                "as_of": response.as_of, "agent_name": response.agent_name, "status": response.status,
                "recommendation": response.recommendation, "bias": response.bias, "confidence": response.confidence,
                "warnings": list(response.warnings)}
        return {"responses": {"ai_structure": ai.ai_structure, "ai_liquidity": ai.ai_liquidity, "ai_macro": ai.ai_macro,
                              "ai_setup_review": ai.ai_setup_review, "ai_trade_review": ai.ai_trade_review},
                "final_status": ai.final_status, "warnings": list(ai.warnings),
                "trade_plan_present": ai.trade_plan is not None, "risk_decision": ai.risk_decision,
                "audit": [{"run_id": e.get("run_id"), "agent": e.get("agent"), "prompt_version": e.get("prompt_version"),
                           "evidence_ids": list(e.get("evidence_ids") or ()), "validation": e.get("validation"),
                           "outcome": e.get("outcome"), "evidence_fingerprint": e.get("evidence_fingerprint"),
                           "called": (e.get("call") or {}).get("called"), "response": identity(e.get("response"))}
                          for e in audit.entries]}

    def run_cycle(self, symbol, scheduled_at):
        rex_holder = []  # V2 P3: the run's REX recorder, created only after the claim (flag ON)
        returned = self._run_cycle(symbol, scheduled_at, rex_holder)
        if rex_holder and rex_holder[0] is not None:
            try:
                rex_holder[0].finish(returned)
            except Exception as exc:  # noqa: BLE001 - REX never changes the cycle result
                LOG.warning("component=rex event=REX_FINISH_FAILED error_type=%s", type(exc).__name__)
        return returned

    def _run_cycle(self, symbol, scheduled_at, rex_holder):
        if symbol not in MARKETS:
            raise ValueError("unsupported operational symbol")
        if symbol not in self.config.enabled_symbols:
            raise ValueError("symbol disabled by configuration")
        slot = slot_at(scheduled_at, self.config.cadence_minutes)
        key = slot_key(symbol, slot, self.config.cadence_minutes)
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        if not self.store.claim_slot(key, symbol, slot, self.clock(), run_id=run_id):
            return "DUPLICATE"
        rex = self._new_rex(run_id, key, symbol, slot)
        rex_store = None
        if rex is not None:
            try:
                from runtime.rex import RexStore
                rex_store = RexStore(self.store, rex)
            except Exception as exc:  # noqa: BLE001 - no REX for this run (reported uncovered by the verifier)
                LOG.warning("component=rex event=REX_UNAVAILABLE error_type=%s", type(exc).__name__)
                rex = None
        rex_holder.append(rex)
        stage = "startup"
        try:
            account_for_metadata, _, _ = self.store.load_paper(self.config.account_id)
            commit = os.environ.get("AI_FLOOR_GIT_COMMIT")
            if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
                raise ValueError("invalid AI_FLOOR_GIT_COMMIT")
            self.store.save_run_metadata(key, self.config.fingerprint(), account_for_metadata.starting_equity,
                                         git_commit=commit)
            session_clock = None  # V2 P3: the same single clock() call as before, kept for REX evidence
            if (not self.diagnostic_outside_session and
                    (not set(session_names(slot)) & set(self.config.sessions) or
                     not set(session_names((session_clock := self.clock()))) & set(self.config.sessions))):
                self._rex_note(rex, "EXIT_SESSION", lambda: {
                    "slot": slot, "session_clock": session_clock, "sessions": list(self.config.sessions),
                    "slot_sessions": list(session_names(slot)),
                    "clock_sessions": None if session_clock is None else list(session_names(session_clock))})
                self.store.event(self.clock(), run_id, symbol, "scheduler", "SESSION_SKIPPED")
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "SESSION_SKIPPED"
            if not set(session_names(slot)) & set(self.config.sessions):
                self.store.event(self.clock(), run_id, symbol, "scheduler", "DIAGNOSTIC_OUTSIDE_SESSION", "INFO")
            if self.market_provider is None:
                self._rex_note(rex, "EXIT_NO_PROVIDER", lambda: {"market_provider_configured": False})
                self.store.event(self.clock(), run_id, symbol, "market_data", "DATA_UNAVAILABLE", "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, "NO_DATA", "provider_not_configured", run_id))
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "NO_DATA"
            stage = "market_data"
            try:
                snapshot = self.market_provider.load_snapshot(symbol, slot)
            except Exception as provider_error:
                from data.massive_provider import MassiveProviderError
                from data.twelve_data_provider import TwelveDataProviderError
                if not isinstance(provider_error, (MassiveProviderError, TwelveDataProviderError)):
                    raise
                provider_state = provider_error.kind
                self._rex_note(rex, "EXIT_PROVIDER_ERROR", lambda: {"provider_state": provider_state})
                with self.store.transaction():
                    self.store.set_state("market_data_provider", provider_state)
                self.store.event(self.clock(), run_id, symbol, "market_data", provider_state, "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, "NO_DATA", provider_state, run_id))
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "NO_DATA"
            paper_blocked = None  # B2.3B: reason PAPER economics are blocked this cycle (in-scope symbols only).
            if self.config.v2_position_catch_up:
                self._last_ingest = None
                if self.config.catch_up_applies(symbol):
                    paper_blocked = self._ingest_evidence(symbol, snapshot, slot)
                else:  # P1-B: outside the scope, evidence is observe-only and never blocks the legacy path
                    self._observe_evidence(symbol, snapshot, slot, run_id)
                if paper_blocked is None and self._last_ingest is not None:
                    # V2 P8.4 R4: REVISION visibility; observability only, never a decision
                    self._audit_safely(lambda: self._record_revisions(symbol, snapshot, run_id), run_id, symbol)
            self._rex_note(rex, "ST1", lambda: {"v2_position_catch_up": self.config.v2_position_catch_up,
                                                "catch_up_applies": self.config.catch_up_applies(symbol),
                                                "paper_blocked": paper_blocked})
            fresh, data_state, last_bar = fresh_snapshot(snapshot, symbol, slot, self.config.max_age_seconds)
            if fresh:
                # The slot bounds evidence; wall time bounds execution freshness.
                fresh, data_state, last_bar = fresh_snapshot(snapshot, symbol, self.clock(), self.config.max_age_seconds)
            if getattr(self.market_provider, "health_by_asset", {}).get(
                {"XAUUSD": "METAL", "EURUSD": "FOREX", "NAS100": "INDEX"}[symbol]
            ) == "STALE":
                fresh, data_state = False, "STALE_DATA"
            self._rex_note(rex, "DATA", lambda: {"fresh": fresh, "data_state": data_state})
            with self.store.transaction():
                self.store.set_state("market_data_provider", "CURRENT" if fresh else data_state)
            self.store.event(self.clock(), run_id, symbol, "market_data", "DATA_CHECK", "INFO" if fresh else "WARNING", {"state": data_state})
            # Passive SYSTEM HEALTH hook (V2 Phase 1): observes the already-decided verdict; never raises.
            health_hooks.emit("market_data_observed", symbol=symbol, slot=slot, snapshot=snapshot,
                              data_state=data_state, provider_mode=self.config.market_provider_mode)
            if not fresh:
                self._rex_note(rex, "EXIT_DATA", lambda: {"data_state": data_state})
                self.store.event(self.clock(), run_id, symbol, "market_data", "DATA_STALE" if data_state == "STALE_DATA" else "DATA_UNAVAILABLE", "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, data_state, "freshness_gate_blocked", run_id))
                self.store.finish(key, self.clock(), "COMPLETED", data_state)
                return data_state
            if not self.store.owns_slot(key, symbol):
                raise RuntimeError("slot ownership lost")
            # A previously authorized paper order may progress only after this
            # cycle's AI review is available and no provider failure is present.
            bar = {"symbol": symbol, "timestamp": snapshot["5m"].index.max().to_pydatetime(),
                   "open": float(last_bar["Open"]), "high": float(last_bar["High"]),
                   "low": float(last_bar["Low"]), "close": float(last_bar["Close"]), "is_closed": True}

            def manage_positions(fresh):
                had_open = bool(fresh.account.open_positions)
                self._rex_note(rex, "ST2L_INPUT", lambda: {"had_open": had_open, "paper_enabled": self.paper_enabled,
                                                           "open_positions": list(fresh.account.open_positions)})
                if self.paper_enabled:
                    TradeManager(fresh.account, fresh).process_bar(bar)
                return self.paper_enabled and (had_open or bool(fresh.journal)), None
            if not self.config.catch_up_applies(symbol):  # P1-B: legacy newest-bar path outside the scope
                self._rex_context(rex, "ST2L", lambda: {"bar": bar, "paper_enabled": self.paper_enabled})
                broker, _ = self._guarded_paper_write(symbol, key, manage_positions, rex_store)
                if broker is None:
                    raise RuntimeError("stale paper state")  # Fail closed: nothing of this cycle written.
            else:  # B2.3B replaces the newest-bar TradeManager step; it never falls back to it.
                if self.paper_enabled and paper_blocked is None:
                    paper_blocked = self._catch_up_positions(symbol, slot, key, rex_store,
                                                             None if rex is None else rex.observer)
                if paper_blocked is not None:
                    self.store.event(self.clock(), run_id, symbol, "market_evidence", paper_blocked, "ERROR")
                broker = self._broker(symbol)
            decision_equity = broker.account.equity
            self._rex_note(rex, "ST3", lambda: {
                "path": "CATCH_UP" if self.config.catch_up_applies(symbol) else "LEGACY",
                "paper_blocked": paper_blocked, "decision_equity": decision_equity,
                "account": {k: getattr(broker.account, k) for k in ("starting_equity", "cash", "equity",
                                                                    "realized_pnl", "unrealized_pnl")},
                "open_positions": list(broker.account.open_positions),
                "pending_on_symbol": [o.order_id for o in broker.orders.values()
                                      if o.symbol == symbol and o.status == "PENDING"]})
            stage = "deterministic"
            deterministic = run_floor(snapshot, slot, symbol, self.macro_provider,
                                      self.instruments.get(symbol), self.risk_config,
                                      equity=decision_equity, run_id=run_id)
            self._rex_note(rex, "ST4", lambda: self._rex_floor(deterministic, symbol, decision_equity))
            deterministic = apply_paper_quantity_increment(deterministic, self.instruments.get(symbol))
            self._rex_note(rex, "F1B", lambda: {
                "final_status": deterministic.final_status, "risk_decision": deterministic.risk_decision,
                "warnings": list(deterministic.warnings),
                "quantity_increment": getattr(self.instruments.get(symbol), "quantity_increment", None)})
            with self.store.transaction():
                self.store.set_state("macro_provider", getattr(self.macro_provider, "health", "NOT CONFIGURED"))
            macro_report = deterministic.macro_news_report
            if macro_report and macro_report.status == "ERROR":
                self.store.event(self.clock(), run_id, symbol, "macro_news", "PROVIDER_FAILURE", "ERROR",
                                 {"provider": self.config.macro_provider_mode})
            if (self.config.macro_provider_mode in {"finnhub", "official_hybrid", "fxmacrodata"} and macro_report and
                    macro_report.status in {"OK", "PARTIAL"} and macro_report.evidence):
                self._audit_safely(lambda: self.store.record_macro_awareness(
                    self.clock(), run_id, symbol, macro_report.evidence[0].get("macro_events", ())),
                    run_id, symbol)
            stage = "ai"
            audit = AuditLog()
            ai_provider = self.ai_provider
            if self.config.v2_ai_resilience:  # V2 P7.1: fail-closed short-circuit + time budget for THIS cycle only
                from ai.resilience import AICycleGuard, GuardedProvider
                ai_provider = GuardedProvider(self.ai_provider, AICycleGuard(self.ai_cycle_budget_seconds),
                                              self.ai_health, clock=self.clock)
            if rex is not None:
                try:
                    from runtime.rex import RexProviderProbe
                    ai_provider = RexProviderProbe(ai_provider, rex)
                except Exception as exc:  # noqa: BLE001 - the unwrapped provider; REX notes the gap
                    rex.fail("ai_probe", exc)
            ai = run_ai(deterministic, ai_provider, audit)
            self._rex_note(rex, "ST5", lambda: self._rex_ai(ai, audit))
            if self.config.v2_ai_resilience:
                health_events = self.ai_health.drain()
                self._audit_safely(lambda: self._record_ai_health(health_events, run_id, symbol), run_id, symbol)
            if not self.store.owns_slot(key, symbol):
                raise RuntimeError("slot ownership lost")
            self.store.save_reports(key, deterministic, ai, audit.entries)
            self._audit_safely(lambda: self.store.record_analysis_events(self.clock(), deterministic, ai),
                               run_id, symbol)
            if self.config.v2_ai_call_audit:  # V2 P7.1: observability only; a failure never changes the cycle.
                self._audit_safely(lambda: self._record_ai_calls(audit, ai, deterministic, symbol, key),
                                   run_id, symbol)
            provider_failed = any(r is not None and r.status == "ERROR" for r in (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review, ai.ai_trade_review))
            with self.store.transaction():
                self.store.set_state("ai_provider", "ERROR" if provider_failed else "DETERMINISTIC" if isinstance(self.ai_provider, DeterministicAIProvider) else "CONFIGURED")
            if provider_failed:
                self.store.event(self.clock(), ai.run_id, symbol, "ai_provider", "PROVIDER_FAILURE", "ERROR")
            # Provider/AI latency may cross a freshness or session boundary.
            # Recheck before progressing any pending order or submitting a new one.
            execution_at = self.clock()
            execution_fresh, execution_state, _ = fresh_snapshot(
                snapshot, symbol, execution_at, self.config.max_age_seconds)
            session_open = self.diagnostic_outside_session or bool(
                set(session_names(execution_at)) & set(self.config.sessions))
            if not session_open:
                execution_state = "SESSION_SKIPPED"
                execution_fresh = False
            self._rex_note(rex, "ST6", lambda: {"execution_at": execution_at, "execution_fresh": execution_fresh,
                                                "execution_state": execution_state, "session_open": session_open})
            if not execution_fresh:
                self._rex_note(rex, "EXIT_EXECUTION_GATE", lambda: {"execution_state": execution_state})
                self.store.event(execution_at, run_id, symbol, "runtime", "EXECUTION_GATE_BLOCKED", "WARNING",
                                 {"state": execution_state})
                self._record_execution_safely(execution_at, run_id, symbol,
                                              deterministic.setup_assessment,
                                              status="SKIPPED", reason=execution_state)
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, execution_state,
                                         "execution_time_gate_blocked", run_id))
                self.store.finish(key, execution_at, "COMPLETED", execution_state)
                return execution_state
            ai_healthy = all(r is not None and r.status in {"OK", "PARTIAL"} for r in
                (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review))
            blocked_before_st7 = paper_blocked
            if self.config.catch_up_applies(symbol):  # P1-B: outside the scope, the legacy pending path below
                # B2.3C: the existing V1 gate values of THIS cycle plus its committed Evidence bar;
                # B2.2 decides. No snapshot fallback, no approval from another cycle.
                if self.paper_enabled and paper_blocked is None:
                    if not self.store.owns_slot(key, symbol):
                        raise RuntimeError("slot ownership lost")
                    committed = self._committed_bar(symbol, bar["timestamp"])
                    if committed is None:
                        paper_blocked = EVIDENCE_UNAVAILABLE
                    else:
                        gate = CurrentCycleGate(run_id=run_id, symbol=symbol, bar=committed,
                                                paper_enabled=self.paper_enabled, execution_fresh=execution_fresh,
                                                session_open=session_open, ai_healthy=ai_healthy,
                                                ai_final_status=ai.final_status)
                        self._rex_note(rex, "ST7_GATE", lambda: {"gate": gate, "gate_passed": gate.passed()})
                        paper_blocked = self._gate_pending_orders(gate, symbol, slot, key, rex_store,
                                                                  None if rex is None else rex.observer)
                    if paper_blocked is not None:
                        self.store.event(self.clock(), run_id, symbol, "market_evidence", paper_blocked, "ERROR")
            elif (self.paper_enabled and paper_blocked is None and ai_healthy
                    and ai.final_status not in {"AI_CAUTION", "ERROR", "RISK_REJECTED"}):
                if not self.store.owns_slot(key, symbol):
                    raise RuntimeError("slot ownership lost")

                def progress_pending(fresh):
                    self._rex_note(rex, "ST7_ORDERS", lambda: {"order_ids": [
                        o.order_id for o in fresh.orders.values() if o.symbol == symbol and o.status == "PENDING"],
                        "current_equity": fresh.account.equity})
                    for order in tuple(fresh.orders.values()):
                        if order.symbol == symbol and order.status == "PENDING" and bar["timestamp"] > order.as_of:
                            fresh.process_next_bar(order, bar)
                    return bool(fresh.journal), None
                self._rex_context(rex, "ST7", lambda: {"path": "LEGACY", "bar": bar})
                if self._guarded_paper_write(symbol, key, progress_pending, rex_store)[0] is None:
                    raise RuntimeError("stale paper state")  # Fail closed: no pending-order effect written.
            self._rex_note(rex, "ST7", lambda: {
                "path": "CATCH_UP" if self.config.catch_up_applies(symbol) else "LEGACY",
                "paper_enabled": self.paper_enabled, "paper_blocked_before": blocked_before_st7,
                "paper_blocked": paper_blocked, "ai_healthy": ai_healthy, "final_status": ai.final_status,
                "ai_healthy_statuses": [None if r is None else r.status for r in
                                        (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review)]})
            broker = self._broker(symbol)  # B2.3A: later decisions read durable state, never a stale broker.
            eligible = paper_policy(ai)
            self._rex_note(rex, "ST8", lambda: {
                "eligible": eligible, "paper_enabled": self.paper_enabled, "paper_blocked": paper_blocked,
                "pending_on_symbol": sorted(o.order_id for o in broker.orders.values()
                                            if o.symbol == symbol and o.status == "PENDING"),
                "open_on_symbol": None if symbol not in broker.account.open_positions
                else broker.account.open_positions[symbol].position_id})
            stage = "paper"
            execution_status, execution_reason = "SKIPPED", "POLICY_NOT_READY"
            blocking_position_id = blocking_order_id = submitted_order_id = None
            if eligible and not self.paper_enabled:
                self._audit_safely(lambda: self.store.event(
                    self.clock(), run_id, symbol, "policy", "PAPER_DIAGNOSTIC_BLOCKED", "INFO"),
                    run_id, symbol)
                execution_reason = "PAPER_DIAGNOSTIC_BLOCKED"
            elif eligible and paper_blocked is not None:
                execution_reason = paper_blocked  # B2.3B: no submission over unreconciled positions.
            elif eligible and not any(o.symbol == symbol and o.status == "PENDING" for o in broker.orders.values()) and symbol not in broker.account.open_positions:
                if not self.store.owns_slot(key, symbol):
                    raise RuntimeError("slot ownership lost")

                def submit(fresh):
                    # The approved Risk decision stays valid only for the state it was sized on:
                    # never resubmit after eligibility or equity changed, and never rerun Risk/AI.
                    self._rex_note(rex, "ST9_RECHECK", lambda: {
                        "pending_on_symbol": any(o.symbol == symbol and o.status == "PENDING"
                                                 for o in fresh.orders.values()),
                        "open_on_symbol": symbol in fresh.account.open_positions,
                        "equity": fresh.account.equity, "decision_equity": decision_equity,
                        "run_order_ids": [o.order_id for o in fresh.orders.values() if o.run_id == run_id]})
                    if (any(o.symbol == symbol and o.status == "PENDING" for o in fresh.orders.values())
                            or symbol in fresh.account.open_positions or fresh.account.equity != decision_equity):
                        return False, STALE_PAPER_STATE
                    order = fresh.submit_plan(deterministic, snapshot, slot)
                    return order is not None, order
                self._rex_context(rex, "ST9", lambda: {"decision_equity": decision_equity, "slot": slot})
                fresh, order = self._guarded_paper_write(symbol, key, submit, rex_store)
                if fresh is None or order is STALE_PAPER_STATE:
                    self._audit_safely(lambda: self.store.event(
                        self.clock(), ai.run_id, symbol, "policy", STALE_PAPER_STATE, "WARNING"),
                        run_id, symbol)
                    execution_reason = STALE_PAPER_STATE
                elif order:
                    self._audit_safely(lambda: self.store.event(
                        self.clock(), ai.run_id, symbol, "policy", "PAPER_ORDER_SUBMITTED"),
                        run_id, symbol)
                    execution_status, execution_reason, submitted_order_id = "SUBMITTED", "ORDER_SUBMITTED", order.order_id
                else:
                    execution_reason = "BROKER_DECLINED_PLAN"
            elif ai.final_status == "AI_CAUTION":
                self._audit_safely(lambda: self.store.event(
                    self.clock(), ai.run_id, symbol, "policy", "AI_CAUTION", "WARNING"),
                    run_id, symbol)
                execution_reason = "AI_CAUTION"
            elif eligible:
                pending = next((o for o in broker.orders.values()
                                if o.symbol == symbol and o.status == "PENDING"), None)
                if pending:
                    execution_reason, blocking_order_id = "PENDING_ORDER", pending.order_id
                else:
                    position = broker.account.open_positions.get(symbol)
                    execution_reason, blocking_position_id = "EXISTING_POSITION", position.position_id
            else:
                execution_reason = ai.final_status if ai.final_status != "PLAN_READY" else "POLICY_REVIEW_UNAVAILABLE"
            self._rex_note(rex, "ST10", lambda: {
                "execution_status": execution_status, "execution_reason": execution_reason,
                "blocking_position_id": blocking_position_id, "blocking_order_id": blocking_order_id,
                "submitted_order_id": submitted_order_id, "final_status": ai.final_status})
            self._record_execution_safely(self.clock(), run_id, symbol,
                                          deterministic.setup_assessment,
                                          status=execution_status, reason=execution_reason,
                                          blocking_position_id=blocking_position_id,
                                          blocking_order_id=blocking_order_id,
                                          order_id=submitted_order_id)
            self._audit_safely(lambda: self.store.save_snapshot(
                symbol, self._snapshot(from_ai_report(ai, now=slot))), run_id, symbol)
            if not self.store.finish(key, self.clock(), "COMPLETED", ai.final_status):
                return "ERROR"
            LOG.info("run_id=%s symbol=%s scheduled_slot=%s component=runtime event=RUN_COMPLETED status=%s",
                     ai.run_id, symbol, utc(slot), ai.final_status)
            return ai.final_status
        except Exception as exc:
            LOG.error("run_id=%s symbol=%s scheduled_slot=%s component=runtime event=RUN_FAILED status=ERROR error_type=%s",
                      run_id, symbol, utc(slot), type(exc).__name__)
            self._rex_note(rex, "EXIT_ERROR", lambda: {"stage": stage, "error_type": type(exc).__name__})
            if stage in {"market_data", "ai"}:
                with self.store.transaction():
                    self.store.set_state("market_data_provider" if stage == "market_data" else "ai_provider", "ERROR")
            self.store.finish(key, self.clock(), "FAILED", "ERROR", type(exc).__name__)
            return "ERROR"
