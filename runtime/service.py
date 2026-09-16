"""One durable PAPER cycle. Explicit dependencies; no network or sample fallback."""
from datetime import datetime, timezone
import logging
import os
import re

from ai.orchestrator import run as run_ai
from ai.provider import DeterministicAIProvider
from data.macro_news import InMemoryMacroNewsProvider
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from floor.orchestrator import run as run_floor
from riesgo import crear_configuracion_riesgo_v2
from runtime.config import RuntimeConfig
from runtime.gates import fresh_snapshot, paper_policy
from runtime.scheduler import session_names, slot_at, slot_key
from storage.codec import utc
from storage.database import Store
from ui.adapters import MARKETS, from_ai_report

LOG = logging.getLogger("ai_floor.runtime")


class OperationalRuntime:
    def __init__(self, config=None, *, market_provider=None, ai_provider=None, macro_provider=None,
                 instruments=None, clock=None, risk_config=None):
        self.config = config or RuntimeConfig.from_env()
        self.market_provider = market_provider
        self.ai_provider = ai_provider or DeterministicAIProvider()
        self.macro_provider = macro_provider or InMemoryMacroNewsProvider()
        self.instruments = instruments or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.risk_config = risk_config or crear_configuracion_riesgo_v2()
        self.store = Store(self.config.db_path)
        self.store.recover(self.clock())
        account, orders, _ = self.store.load_paper(self.config.account_id)
        if account is None:
            if orders or self.store.db.execute("SELECT 1 FROM paper_positions LIMIT 1").fetchone():
                self.store.event(self.clock(), None, None, "recovery", "STATE_INCONSISTENCY", "ERROR", {"reason": "paper_account_missing"})
                self.store.close()
                raise RuntimeError("paper state without account")
            account = PaperAccount("1.0", self.config.account_id, self.config.starting_equity,
                                   self.config.starting_equity, self.config.starting_equity)
            self.store.save_paper(PaperBroker(account))
        for position in account.open_positions.values():
            origin = orders.get(position.origin_order_id)
            if origin is None or origin.status != "FILLED" or origin.symbol != position.symbol or origin.quantity != position.quantity:
                self.store.event(self.clock(), position.run_id, position.symbol, "recovery", "STATE_INCONSISTENCY", "ERROR", {"reason": "orphan_position"})
                self.store.close()
                raise RuntimeError("inconsistent paper position")
        with self.store.transaction():
            self.store.set_state("account_id", self.config.account_id)
            self.store.set_state("market_data_provider", "NOT CONFIGURED" if market_provider is None else "CONFIGURED")
            self.store.set_state("ai_provider", "DETERMINISTIC" if isinstance(self.ai_provider, DeterministicAIProvider) else "CONFIGURED")
        self.store.heartbeat(self.clock(), "STOPPED")

    def close(self):
        self.store.heartbeat(self.clock(), "STOPPED")
        self.store.close()

    def _broker(self, symbol):
        account, orders, fills = self.store.load_paper(self.config.account_id)
        broker = PaperBroker(account, self.instruments.get(symbol))
        broker.orders = orders
        broker.fills = fills
        return broker

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

    def _status_snapshot(self, symbol, slot, state, warning):
        return {"schema_version": "1.0", "run_id": None, "symbol": symbol, "as_of": utc(slot),
                "state": state, "setup_status": "NO_SETUP", "setup_side": None,
                "setup_evidence": [], "setup_invalidation": None, "risk_status": "NOT CALLED",
                "warnings": [warning], "facts": [f"{symbol}: {state}", "Risk Engine: NOT CALLED"],
                "interpretation": [], "freshness": state, "agents": [], "prompt_versions": []}

    def run_cycle(self, symbol, scheduled_at):
        if symbol not in self.config.symbols or symbol not in MARKETS:
            raise ValueError("unsupported operational symbol")
        slot = slot_at(scheduled_at, self.config.cadence_minutes)
        key = slot_key(symbol, slot, self.config.cadence_minutes)
        if not self.store.claim_slot(key, symbol, slot, self.clock()):
            return "DUPLICATE"
        stage = "startup"
        try:
            account_for_metadata, _, _ = self.store.load_paper(self.config.account_id)
            commit = os.environ.get("AI_FLOOR_GIT_COMMIT")
            if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
                raise ValueError("invalid AI_FLOOR_GIT_COMMIT")
            self.store.save_run_metadata(key, self.config.fingerprint(), account_for_metadata.starting_equity,
                                         git_commit=commit)
            if not set(session_names(slot)) & set(self.config.sessions):
                self.store.event(self.clock(), None, symbol, "scheduler", "SESSION_SKIPPED")
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "SESSION_SKIPPED"
            if self.market_provider is None:
                self.store.event(self.clock(), None, symbol, "market_data", "DATA_UNAVAILABLE", "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, "NO_DATA", "provider_not_configured"))
                self.store.finish(key, self.clock(), "COMPLETED", "NO_DATA")
                return "NO_DATA"
            stage = "market_data"
            snapshot = self.market_provider.load_snapshot(symbol, slot)
            fresh, data_state, last_bar = fresh_snapshot(snapshot, symbol, slot, self.config.max_age_seconds)
            with self.store.transaction():
                self.store.set_state("market_data_provider", "CURRENT" if fresh else data_state)
            self.store.event(self.clock(), None, symbol, "market_data", "DATA_CHECK", "INFO" if fresh else "WARNING", {"state": data_state})
            if not fresh:
                self.store.event(self.clock(), None, symbol, "market_data", "DATA_STALE" if data_state == "STALE_DATA" else "DATA_UNAVAILABLE", "WARNING")
                self.store.save_snapshot(symbol, self._status_snapshot(symbol, slot, data_state, "freshness_gate_blocked"))
                self.store.finish(key, self.clock(), "COMPLETED", data_state)
                return data_state
            broker = self._broker(symbol)
            if not self.store.owns_slot(key, symbol):
                raise RuntimeError("slot ownership lost")
            # A previously authorized paper order may progress only after this
            # cycle's AI review is available and no provider failure is present.
            bar = {"symbol": symbol, "timestamp": snapshot["5m"].index.max().to_pydatetime(),
                   "open": float(last_bar["Open"]), "high": float(last_bar["High"]),
                   "low": float(last_bar["Low"]), "close": float(last_bar["Close"]), "is_closed": True}
            had_open = bool(broker.account.open_positions)
            TradeManager(broker.account, broker).process_bar(bar)
            if had_open or broker.journal:
                self.store.save_paper(broker, owner_key=key, symbol=symbol)
            stage = "deterministic"
            deterministic = run_floor(snapshot, slot, symbol, self.macro_provider,
                                      self.instruments.get(symbol), self.risk_config, equity=broker.account.equity)
            stage = "ai"
            ai = run_ai(deterministic, self.ai_provider)
            if not self.store.owns_slot(key, symbol):
                raise RuntimeError("slot ownership lost")
            self.store.save_reports(key, deterministic, ai)
            self.store.record_analysis_events(self.clock(), deterministic, ai)
            provider_failed = any(r is not None and r.status == "ERROR" for r in (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review, ai.ai_trade_review))
            with self.store.transaction():
                self.store.set_state("ai_provider", "ERROR" if provider_failed else "DETERMINISTIC" if isinstance(self.ai_provider, DeterministicAIProvider) else "CONFIGURED")
            if provider_failed:
                self.store.event(self.clock(), ai.run_id, symbol, "ai_provider", "PROVIDER_FAILURE", "ERROR")
            ai_healthy = all(r is not None and r.status in {"OK", "PARTIAL"} for r in
                (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review))
            if ai_healthy and ai.final_status not in {"AI_CAUTION", "ERROR", "RISK_REJECTED"}:
                if not self.store.owns_slot(key, symbol):
                    raise RuntimeError("slot ownership lost")
                for order in tuple(broker.orders.values()):
                    if order.symbol == symbol and order.status == "PENDING" and bar["timestamp"] > order.as_of:
                        broker.process_next_bar(order, bar)
            if broker.journal:
                self.store.save_paper(broker, owner_key=key, symbol=symbol)
            eligible = paper_policy(ai)
            stage = "paper"
            if eligible and not any(o.symbol == symbol and o.status == "PENDING" for o in broker.orders.values()) and symbol not in broker.account.open_positions:
                if not self.store.owns_slot(key, symbol):
                    raise RuntimeError("slot ownership lost")
                order = broker.submit_plan(deterministic, snapshot, slot)
                if order:
                    self.store.save_paper(broker, owner_key=key, symbol=symbol)
                    self.store.event(self.clock(), ai.run_id, symbol, "policy", "PAPER_ORDER_SUBMITTED")
            elif ai.final_status == "AI_CAUTION":
                self.store.event(self.clock(), ai.run_id, symbol, "policy", "AI_CAUTION", "WARNING")
            vm = from_ai_report(ai, now=slot)
            self.store.save_snapshot(symbol, self._snapshot(vm))
            if not self.store.finish(key, self.clock(), "COMPLETED", ai.final_status):
                return "ERROR"
            LOG.info("run_id=%s symbol=%s scheduled_slot=%s component=runtime event=RUN_COMPLETED status=%s",
                     ai.run_id, symbol, utc(slot), ai.final_status)
            return ai.final_status
        except Exception as exc:
            LOG.exception("run_id=unknown symbol=%s scheduled_slot=%s component=runtime event=RUN_FAILED status=ERROR",
                          symbol, utc(slot))
            if stage in {"market_data", "ai"}:
                with self.store.transaction():
                    self.store.set_state("market_data_provider" if stage == "market_data" else "ai_provider", "ERROR")
            self.store.finish(key, self.clock(), "FAILED", "ERROR", type(exc).__name__)
            return "ERROR"
