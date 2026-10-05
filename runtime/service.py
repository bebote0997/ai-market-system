"""One durable PAPER cycle. Explicit dependencies; no network or sample fallback."""
from datetime import datetime, timezone
import logging
import os
import re
import json
import uuid

from ai.orchestrator import run as run_ai
from ai.provider import DeterministicAIProvider
from ai.runtime import AuditLog
from data.macro_news import InMemoryMacroNewsProvider, NoMacroDataProvider
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
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
                 diagnostic_outside_session=False, recovery_stale_after_seconds=120):
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

    def _guarded_paper_write(self, symbol, key, apply):
        """B2.3A: run ``apply(broker)`` on freshly loaded durable PAPER state and persist it only if
        that state is still current (B2.1 ``expected_state``, revalidated under BEGIN IMMEDIATE).

        ``apply`` returns (save, result). On STALE the computation is discarded and redone once from
        a fresh load. Returns (broker, result), or (None, None) when both attempts were stale.
        """
        for _ in range(2):
            broker = self._broker(symbol)
            expected_state = self.store.paper_state(broker.account, broker.orders, broker.fills)
            save, result = apply(broker)
            if not save or self.store.save_paper(broker, owner_key=key, symbol=symbol,
                                                 expected_state=expected_state) is not False:
                return broker, result
        return None, None

    def _ingest_evidence(self, symbol, snapshot, slot):
        """B2.3B: commit this snapshot's closed bars to the separate Evidence Store (closed by the
        slot, the provider's own boundary). Returns None, or the failure reason (fail closed)."""
        try:
            if self.evidence is None:
                from data.market_evidence import MarketEvidenceEngine
                from storage.evidence_store import EvidenceStore
                self.evidence = MarketEvidenceEngine(EvidenceStore(self.config.market_evidence_path),
                                                     enabled_symbols=self.config.enabled_symbols)
            self.evidence.ingest_snapshot(symbol, snapshot, as_of=slot)
            return None
        except Exception as exc:  # noqa: BLE001 - any evidence failure blocks PAPER economics
            LOG.warning("symbol=%s component=market_evidence event=%s error_type=%s",
                        symbol, EVIDENCE_UNAVAILABLE, type(exc).__name__)
            return EVIDENCE_UNAVAILABLE

    def _catch_up_positions(self, symbol, slot, key):
        """B2.3B: every committed closed 5m bar after the open position's durable watermark, oldest
        first, through the accepted B2.1 catch-up (guarded save per bar). Never a newest-bar fallback.
        Returns None, or the reason PAPER economics are blocked for this cycle."""
        for _ in range(2):  # A STALE result resumes from the durable watermark once.
            try:
                result = catch_up_position(self.store, self.evidence, account_id=self.config.account_id,
                                           symbol=symbol, as_of=slot, instrument=self.instruments.get(symbol),
                                           owner_key=key)
            except CatchUpEvidenceError:
                return EVIDENCE_UNAVAILABLE
            if result.status != "STALE":
                return None
        return STALE_PAPER_STATE

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

    def run_cycle(self, symbol, scheduled_at):
        if symbol not in MARKETS:
            raise ValueError("unsupported operational symbol")
        if symbol not in self.config.enabled_symbols:
            raise ValueError("symbol disabled by configuration")
        slot = slot_at(scheduled_at, self.config.cadence_minutes)
        key = slot_key(symbol, slot, self.config.cadence_minutes)
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        if not self.store.claim_slot(key, symbol, slot, self.clock(), run_id=run_id):
            return "DUPLICATE"
        stage = "startup"
        try:
            account_for_metadata, _, _ = self.store.load_paper(self.config.account_id)
            commit = os.environ.get("AI_FLOOR_GIT_COMMIT")
            if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
                raise ValueError("invalid AI_FLOOR_GIT_COMMIT")
            self.store.save_run_metadata(key, self.config.fingerprint(), account_for_metadata.starting_equity,
                                         git_commit=commit)
            if (not self.diagnostic_outside_session and
                    (not set(session_names(slot)) & set(self.config.sessions) or
                     not set(session_names(self.clock())) & set(self.config.sessions))):
                self.store.event(self.clock(), run_id, symbol, "scheduler", "SESSION_SKIPPED")
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "SESSION_SKIPPED"
            if not set(session_names(slot)) & set(self.config.sessions):
                self.store.event(self.clock(), run_id, symbol, "scheduler", "DIAGNOSTIC_OUTSIDE_SESSION", "INFO")
            if self.market_provider is None:
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
                with self.store.transaction():
                    self.store.set_state("market_data_provider", provider_state)
                self.store.event(self.clock(), run_id, symbol, "market_data", provider_state, "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, "NO_DATA", provider_state, run_id))
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "NO_DATA"
            paper_blocked = None  # B2.3B: reason PAPER economics are blocked this cycle (flag ON only).
            if self.config.v2_position_catch_up:
                paper_blocked = self._ingest_evidence(symbol, snapshot, slot)
            fresh, data_state, last_bar = fresh_snapshot(snapshot, symbol, slot, self.config.max_age_seconds)
            if fresh:
                # The slot bounds evidence; wall time bounds execution freshness.
                fresh, data_state, last_bar = fresh_snapshot(snapshot, symbol, self.clock(), self.config.max_age_seconds)
            if getattr(self.market_provider, "health_by_asset", {}).get(
                {"XAUUSD": "METAL", "EURUSD": "FOREX", "NAS100": "INDEX"}[symbol]
            ) == "STALE":
                fresh, data_state = False, "STALE_DATA"
            with self.store.transaction():
                self.store.set_state("market_data_provider", "CURRENT" if fresh else data_state)
            self.store.event(self.clock(), run_id, symbol, "market_data", "DATA_CHECK", "INFO" if fresh else "WARNING", {"state": data_state})
            # Passive SYSTEM HEALTH hook (V2 Phase 1): observes the already-decided verdict; never raises.
            health_hooks.emit("market_data_observed", symbol=symbol, slot=slot, snapshot=snapshot,
                              data_state=data_state, provider_mode=self.config.market_provider_mode)
            if not fresh:
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
                if self.paper_enabled:
                    TradeManager(fresh.account, fresh).process_bar(bar)
                return self.paper_enabled and (had_open or bool(fresh.journal)), None
            if not self.config.v2_position_catch_up:
                broker, _ = self._guarded_paper_write(symbol, key, manage_positions)
                if broker is None:
                    raise RuntimeError("stale paper state")  # Fail closed: nothing of this cycle written.
            else:  # B2.3B replaces the newest-bar TradeManager step; it never falls back to it.
                if self.paper_enabled and paper_blocked is None:
                    paper_blocked = self._catch_up_positions(symbol, slot, key)
                if paper_blocked is not None:
                    self.store.event(self.clock(), run_id, symbol, "market_evidence", paper_blocked, "ERROR")
                broker = self._broker(symbol)
            decision_equity = broker.account.equity
            stage = "deterministic"
            deterministic = run_floor(snapshot, slot, symbol, self.macro_provider,
                                      self.instruments.get(symbol), self.risk_config,
                                      equity=decision_equity, run_id=run_id)
            deterministic = apply_paper_quantity_increment(deterministic, self.instruments.get(symbol))
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
            ai = run_ai(deterministic, self.ai_provider, audit)
            if not self.store.owns_slot(key, symbol):
                raise RuntimeError("slot ownership lost")
            self.store.save_reports(key, deterministic, ai, audit.entries)
            self._audit_safely(lambda: self.store.record_analysis_events(self.clock(), deterministic, ai),
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
            if not self.diagnostic_outside_session and not set(session_names(execution_at)) & set(self.config.sessions):
                execution_state = "SESSION_SKIPPED"
                execution_fresh = False
            if not execution_fresh:
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
            if (self.paper_enabled and paper_blocked is None and ai_healthy
                    and ai.final_status not in {"AI_CAUTION", "ERROR", "RISK_REJECTED"}):
                if not self.store.owns_slot(key, symbol):
                    raise RuntimeError("slot ownership lost")

                def progress_pending(fresh):
                    for order in tuple(fresh.orders.values()):
                        if order.symbol == symbol and order.status == "PENDING" and bar["timestamp"] > order.as_of:
                            fresh.process_next_bar(order, bar)
                    return bool(fresh.journal), None
                if self._guarded_paper_write(symbol, key, progress_pending)[0] is None:
                    raise RuntimeError("stale paper state")  # Fail closed: no pending-order effect written.
            broker = self._broker(symbol)  # B2.3A: later decisions read durable state, never a stale broker.
            eligible = paper_policy(ai)
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
                    if (any(o.symbol == symbol and o.status == "PENDING" for o in fresh.orders.values())
                            or symbol in fresh.account.open_positions or fresh.account.equity != decision_equity):
                        return False, STALE_PAPER_STATE
                    order = fresh.submit_plan(deterministic, snapshot, slot)
                    return order is not None, order
                fresh, order = self._guarded_paper_write(symbol, key, submit)
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
            if stage in {"market_data", "ai"}:
                with self.store.transaction():
                    self.store.set_state("market_data_provider" if stage == "market_data" else "ai_provider", "ERROR")
            self.store.finish(key, self.clock(), "FAILED", "ERROR", type(exc).__name__)
            return "ERROR"
