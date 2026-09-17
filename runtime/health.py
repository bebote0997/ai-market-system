"""Read-only local health projection; heartbeat is separate from market freshness."""
from datetime import datetime, timezone
import json

from storage.codec import parse_utc
from storage.database import SCHEMA_VERSION


def health(store, now=None, heartbeat_limit_seconds=60):
    now = now or datetime.now(timezone.utc)
    heartbeat = store.get_state("heartbeat")
    scheduler = store.get_state("scheduler") or "NOT STARTED"
    alive = bool(heartbeat and 0 <= (now - parse_utc(heartbeat)).total_seconds() <= heartbeat_limit_seconds and scheduler == "RUNNING")
    last = store.latest_run()
    latest_snapshot = store.load_snapshot(last["symbol"]) if last else None
    snapshot_freshness = latest_snapshot.get("freshness", "NO_DATA") if latest_snapshot else "NO_DATA"
    if latest_snapshot and latest_snapshot.get("as_of") and snapshot_freshness == "CURRENT" and (now - parse_utc(latest_snapshot["as_of"])).total_seconds() > 900:
        snapshot_freshness = "STALE_DATA"
    market_provider = store.get_state("market_data_provider") or "NOT CONFIGURED"
    market_provider_mode = store.get_state("market_provider_mode") or "none"
    from runtime.config import DEFAULT_ENABLED_SYMBOLS, SUPPORTED_SYMBOLS
    enabled_symbols = json.loads(store.get_state("enabled_symbols") or json.dumps(DEFAULT_ENABLED_SYMBOLS))
    degraded = bool(last and last["status"] == "FAILED") or snapshot_freshness in {"STALE_DATA", "NO_DATA"} or market_provider == "NOT CONFIGURED"
    status = "STOPPED" if scheduler in {"STOPPED", "DISABLED", "NOT STARTED"} else "DEGRADED" if not alive or degraded else "ONLINE"
    return {"status": status,
            "supported_symbols": SUPPORTED_SYMBOLS, "enabled_symbols": tuple(enabled_symbols),
            "database": "ONLINE", "schema_version": SCHEMA_VERSION,
            "runtime_heartbeat": heartbeat or "NO_DATA", "scheduler": scheduler,
            "market_data": market_provider,
            "market_provider_mode": market_provider_mode,
            "massive_adapter": "SUPPORTED / NOT ACTIVE" if market_provider_mode != "massive" else "ACTIVE",
            "ai_provider": store.get_state("ai_provider") or "NOT CONFIGURED", "paper_broker": "PAPER",
            "last_run": store.get_state("last_run") or "NO_DATA",
            "last_success": store.get_state("last_success") or "NO_DATA",
            "freshness": snapshot_freshness}
