"""Clock-injected 15-minute slots and DST-aware analysis windows."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from storage.codec import utc

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
        self.runtime.store.heartbeat(now, "RUNNING" if config.scheduler_enabled else "DISABLED")
        if not config.scheduler_enabled:
            return []
        slot = slot_at(now, config.cadence_minutes)
        previous = self.runtime.store.get_state("scheduler_slot")
        if previous:
            from storage.codec import parse_utc
            missed = int((slot - parse_utc(previous)).total_seconds() // (config.cadence_minutes * 60)) - 1
            if missed > 0:
                self.runtime.store.event(now, None, None, "scheduler", "SLOTS_MISSED", "WARNING", {"count": missed})
        with self.runtime.store.transaction():
            self.runtime.store.set_state("scheduler_slot", utc(slot))
        if not set(session_names(slot)) & set(config.sessions):
            return []
        return [self.runtime.run_cycle(symbol, slot) for symbol in config.enabled_symbols]
