"""Clock-injected 15-minute slots and DST-aware analysis windows."""
from datetime import timezone
import logging
from zoneinfo import ZoneInfo

from runtime import symbol_pause
from storage.codec import utc

LOG = logging.getLogger("ai_floor.runtime")
ZONES = {"LONDON": ZoneInfo("Europe/London"), "NEW_YORK": ZoneInfo("America/New_York")}


def slot_at(at, cadence_minutes=15):
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("aware scheduler timestamp required")
    at = at.astimezone(timezone.utc)
    return at.replace(minute=(at.minute // cadence_minutes) * cadence_minutes, second=0, microsecond=0)


def session_names(at):
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("aware scheduler timestamp required")
    active = []
    for name, zone in ZONES.items():
        local = at.astimezone(zone)
        if local.weekday() < 5 and 8 <= local.hour < 17:
            active.append(name)
    return tuple(active)


def slot_key(symbol, slot, cadence_minutes):
    return f"{symbol}:{utc(slot)}:{cadence_minutes}m"


class Scheduler:
    def __init__(self, runtime, clock):
        self.runtime = runtime
        self.clock = clock

    def tick(self):
        now = self.clock()
        config = self.runtime.config
        gate = getattr(self.runtime, "halt_gate", None)  # V2 P4a R-HALT; None = legacy (flag OFF)
        self.runtime.store.heartbeat(now, "RUNNING" if config.scheduler_enabled else "DISABLED")  # P: allowed
        if not config.scheduler_enabled:
            return []
        slot = slot_at(now, config.cadence_minutes)
        previous = self.runtime.store.get_state("scheduler_slot")
        if previous:
            from storage.codec import parse_utc
            missed = int((slot - parse_utc(previous)).total_seconds() // (config.cadence_minutes * 60)) - 1
            if missed > 0 and (gate is None or gate.allow("O")):
                self.runtime.store.event(now, None, None, "scheduler", "SLOTS_MISSED", "WARNING", {"count": missed})
        if gate is None or gate.allow("O"):
            with self.runtime.store.transaction():
                self.runtime.store.set_state("scheduler_slot", utc(slot))
        symbols = []
        for symbol in config.enabled_symbols:
            if symbol_pause.is_paused(symbol):  # DEC-XAU-OFF-1: never a cycle (no claim, no AI, no alerts)
                symbol_pause.log_paused_once(symbol)
            else:
                symbols.append(symbol)
        if not set(session_names(slot)) & set(config.sessions):
            # Pre-demo trade-duration rule: the 17:00 NY close falls outside every session, so open positions
            # are checked here too (no cycle, no slot claim). Never raises into the scheduler.
            manage = getattr(self.runtime, "manage_trade_duration", None)
            if manage is not None and (gate is None or not gate.halted):
                try:
                    manage(slot)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("component=trade_duration event=TRADE_DURATION_FAILED error_type=%s",
                                type(exc).__name__)
            return []
        # DEC-XAU-OFF-1: flatten paused symbols (PAPER, simulated) before the cycles; out of session the
        # manage pass above does it. Never raises into the scheduler.
        close_paused = getattr(self.runtime, "close_paused_symbols", None)
        if close_paused is not None and (gate is None or not gate.halted):
            try:
                close_paused(slot)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("component=symbol_pause event=PAUSED_CLOSE_FAILED error_type=%s", type(exc).__name__)
        if gate is None:
            results = [self.runtime.run_cycle(symbol, slot) for symbol in symbols]
        else:
            results = []
            for symbol in symbols:
                if gate.halted:  # design 3.8 row 6: no new claim once T_h happened (non-economic check)
                    break
                results.append(self.runtime.run_cycle(symbol, slot))
        flush = getattr(self.runtime, "flush_evidence_observations", None)
        if flush is not None:  # Phase 13: catch-up-OFF observe-only evidence, after every cycle of the tick
            flush()
        return results
