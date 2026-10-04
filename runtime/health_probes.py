"""SYSTEM HEALTH V2 probes over EXISTING persisted evidence (F01-T01..T14 preparation).

Each probe READS the trading database read-only (storage.database.Store(readonly=True)),
NORMALIZES what it finds, and WRITES only to the SYSTEM HEALTH sidecar via SystemHealth.
Probes never own, call or reconfigure the component they observe: no provider requests,
no orders, no risk evaluation, no schema change, no clock (``now`` is always supplied).

Thresholds: probes apply no timing policy of their own. Market-data freshness is the
verdict the runtime already recorded with its configured max_age_seconds. Every other
threshold lives in the caller-supplied HealthPolicy (OWNER DECISION REQUIRED).
Observation ids are deterministic digests of the evidence, so re-collection is idempotent.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

from runtime.system_health import ComponentErrorType, HealthStatus, SystemHealth, sanitize_reason
from storage.codec import parse_utc
from storage.database import SCHEMA_VERSION, Store
from storage.health_store import HealthStore

AGENT_COMPONENTS = ("structure_ai", "liquidity_ai", "macro_ai", "setup_reviewer_ai", "trade_reviewer_ai")
MARKET_DATA_EVENTS = frozenset({"DATA_CHECK", "DATA_UNAVAILABLE"})
MARKET_NON_ERROR_EVENTS = frozenset({"DATA_CHECK", "DATA_UNAVAILABLE", "DATA_STALE"})
PAPER_BUSINESS_EVENTS = frozenset({"ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED", "ORDER_REJECTED",
                                   "ORDER_CANCELLED", "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"})
# A broker rejection is a business outcome EXCEPT when the order contract itself was invalid.
PAPER_OPERATIONAL_REJECTIONS = frozenset({"invalid_order_contract"})
EMAIL_STATUS = {"component": "email", "provider": "", "status": HealthStatus.UNKNOWN.value,
                "reason": "NOT_IMPLEMENTED", "detail": "No email transport exists before Phase 9."}


def observation_id(*parts):
    return "obs:" + hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:40]


def _json(text, default):
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _ms(started, completed):
    if not started or not completed:
        return None
    value = (parse_utc(completed) - parse_utc(started)).total_seconds() * 1000
    return value if value >= 0 else None


# --- F01-T01 scheduler / pipeline progress ---------------------------------------------------------------
def probe_scheduler(trading, health):
    heartbeat = trading.get_state("heartbeat")
    scheduler_state = trading.get_state("scheduler") or "NOT STARTED"
    if heartbeat:
        health.record_heartbeat("scheduler", at=parse_utc(heartbeat), observation_id=observation_id("hb", heartbeat))
    details = {"scheduler_state": scheduler_state, "last_slot": trading.get_state("scheduler_slot")}
    runs = trading.db.execute("SELECT slot_key, run_id, started_at, completed_at, status, final_status, error "
                              "FROM runs WHERE completed_at IS NOT NULL ORDER BY completed_at, slot_key").fetchall()
    for run in runs:
        at = parse_utc(run["completed_at"])
        oid = observation_id("run", run["slot_key"], run["status"], run["completed_at"])
        latency = _ms(run["started_at"], run["completed_at"])
        health.record_progress("scheduler", at=at, observation_id=observation_id("progress", oid),
                               stage="run_" + run["status"].lower(), reference=run["run_id"])
        if run["status"] == "COMPLETED":
            health.record_success("scheduler", at=at, observation_id=oid, latency_ms=latency,
                                  details={**details, "final_status": run["final_status"]})
        else:
            health.record_error("scheduler", at=at, observation_id=oid, latency_ms=latency,
                                component_error=ComponentErrorType.SCHEDULER_ERROR,
                                reason=f"run {run['status']}: {run['error'] or 'no error recorded'}",
                                details={**details, "final_status": run["final_status"]})


# --- F01-T02 market data -------------------------------------------------------------------------------------
def probe_market_data(trading, health):
    provider = trading.get_state("market_provider_mode") or "unknown"
    rows = trading.db.execute("SELECT id, timestamp, run_id, symbol, event_type, payload FROM journal "
                              "WHERE source='market_data' ORDER BY id").fetchall()
    for row in rows:
        if row["event_type"] == "DATA_STALE":
            continue  # Duplicates the DATA_CHECK verdict of the same cycle.
        component = f"market_data:{row['symbol'] or 'unknown'}"
        at, oid = parse_utc(row["timestamp"]), observation_id("market", row["id"])
        payload = _json(row["payload"], {})
        details = {"run_id": row["run_id"], "symbol": row["symbol"]}
        if row["event_type"] in MARKET_DATA_EVENTS:
            state = payload.get("state", "NO_DATA") if row["event_type"] == "DATA_CHECK" else "NO_DATA"
            details["data_state"] = state
            if state == "CURRENT":
                health.record_success(component, provider=provider, at=at, observation_id=oid, details=details)
            elif state == "STALE_DATA":
                health.record_state(component, provider=provider, at=at, observation_id=oid,
                                    status=HealthStatus.STALE, reason="STALE_DATA", details=details)
            else:
                health.record_state(component, provider=provider, at=at, observation_id=oid,
                                    status=HealthStatus.UNKNOWN, reason="NO_DATA", details=details)
        elif row["event_type"] not in MARKET_NON_ERROR_EVENTS:
            details["data_state"] = "PROVIDER_FAILURE"
            health.record_error(component, provider=provider, at=at, observation_id=oid, kind=row["event_type"],
                                reason=f"market data provider: {row['event_type']}", details=details)


# --- F01-T03 macro data (independent of Macro AI) ------------------------------------------------------------
def probe_macro_data(trading, health):
    mode = trading.get_state("macro_provider_mode") or "none"
    current_health = trading.get_state("macro_provider")
    rows = trading.db.execute("SELECT id, timestamp, run_id, symbol, source, event_type, payload FROM journal "
                              "WHERE (source='macro' AND event_type='MACRO_COMPLETE') OR "
                              "(source='macro_news' AND event_type='PROVIDER_FAILURE') ORDER BY id").fetchall()
    failed_runs = {r["run_id"] for r in rows if r["event_type"] == "PROVIDER_FAILURE"}
    latest_failure = max((r["id"] for r in rows if r["event_type"] == "PROVIDER_FAILURE"), default=None)
    for row in rows:
        at, oid = parse_utc(row["timestamp"]), observation_id("macro", row["id"])
        details = {"run_id": row["run_id"], "symbol": row["symbol"], "provider_mode": mode}
        if row["event_type"] == "PROVIDER_FAILURE":
            # The typed kind is only retained as the provider's CURRENT health state.
            kind = current_health if row["id"] == latest_failure else None
            health.record_error("macro_data", provider=mode, at=at, observation_id=oid, kind=kind,
                                reason="macro provider failure", details={**details, "data_state": "PROVIDER_FAILURE"})
            continue
        if row["run_id"] in failed_runs:
            continue  # That run's verdict is the failure above.
        state = _json(row["payload"], {}).get("status")
        if mode == "none":
            health.record_state("macro_data", provider=mode, at=at, observation_id=oid, status=HealthStatus.UNKNOWN,
                                reason="NOT_CONFIGURED", details={**details, "data_state": "NOT_CONFIGURED"})
        else:  # RECORDED, or a legitimate NO_DATA (no relevant events): both are healthy operation.
            health.record_success("macro_data", provider=mode, at=at, observation_id=oid,
                                  details={**details, "data_state": "VALID" if state == "RECORDED" else "NO_DATA"})


# --- F01-T04..T08 AI provider and agents (from persisted agent decisions) --------------------------------------
def probe_ai(trading, health):
    rows = trading.db.execute(
        "SELECT a.slot_key, a.agent_name, a.status, a.warnings, a.model_metadata, r.run_id, r.started_at, "
        "r.completed_at FROM agent_decisions a JOIN runs r ON r.slot_key=a.slot_key "
        "ORDER BY COALESCE(r.completed_at, r.started_at), a.slot_key, a.agent_name").fetchall()
    per_run = {}
    for row in rows:
        metadata = _json(row["model_metadata"], {})
        provider = str(metadata.get("provider") or "")
        warnings = [w for w in _json(row["warnings"], []) if isinstance(w, str)]
        at = parse_utc(row["completed_at"] or row["started_at"])
        oid = observation_id("agent", row["slot_key"], row["agent_name"], row["status"])
        component = row["agent_name"] if row["agent_name"] in AGENT_COMPONENTS else "unknown_ai_agent"
        details = {"run_id": row["run_id"], "model": metadata.get("model"), "result_class": row["status"]}
        provider_exception = any(w.startswith("provider_exception") for w in warnings)
        if row["status"] in {"OK", "PARTIAL", "NO_DATA"}:
            health.record_success(component, provider=provider, at=at, observation_id=oid, details=details)
        elif provider_exception:
            health.record_error(component, provider=provider, at=at, observation_id=oid,
                                reason=warnings[0], details=details)  # Typed kind is not persisted.
        else:  # Response rejected by grounding/contract validation.
            health.record_error(component, provider=provider, at=at, observation_id=oid, kind="INVALID_RESPONSE",
                                reason=warnings[0] if warnings else "invalid agent response", details=details)
        if provider and provider not in {"deterministic", "fake"} and row["status"] != "NO_DATA":
            run = per_run.setdefault((row["slot_key"], provider), {"at": at, "failed": False, "model": None,
                                                                   "run_id": row["run_id"]})
            run["failed"] |= provider_exception
            run["model"] = run["model"] or metadata.get("model")
    for (slot_key, provider), run in per_run.items():
        oid = observation_id("ai_provider", slot_key, provider)
        details = {"run_id": run["run_id"], "model": run["model"]}
        if run["failed"]:
            health.record_error("ai_provider", provider=provider, at=run["at"], observation_id=oid,
                                reason="provider exception in at least one agent call", details=details)
        else:
            health.record_success("ai_provider", provider=provider, at=run["at"], observation_id=oid, details=details)


# --- F01-T09 risk engine (operational health only) -------------------------------------------------------------
def probe_risk(trading, health):
    rows = trading.db.execute("SELECT d.slot_key, d.status, r.run_id, r.started_at, r.completed_at "
                              "FROM risk_decisions d JOIN runs r ON r.slot_key=d.slot_key "
                              "ORDER BY COALESCE(r.completed_at, r.started_at), d.slot_key").fetchall()
    for row in rows:
        at = parse_utc(row["completed_at"] or row["started_at"])
        oid = observation_id("risk", row["slot_key"], row["status"])
        details = {"run_id": row["run_id"], "decision": row["status"]}
        if row["status"] in {"APPROVED", "REJECTED"}:  # Both are successful evaluations.
            health.record_success("risk_engine", at=at, observation_id=oid, details=details)
        else:
            health.record_error("risk_engine", at=at, observation_id=oid, details=details,
                                component_error=ComponentErrorType.RISK_ENGINE_ERROR,
                                reason=f"unexpected risk decision status {row['status']}")


# --- F01-T10 paper broker ----------------------------------------------------------------------------------------
def probe_paper_broker(trading, health):
    placeholders = ",".join("?" * len(PAPER_BUSINESS_EVENTS))
    rows = trading.db.execute(f"SELECT id, timestamp, run_id, symbol, event_type, payload FROM journal "
                              f"WHERE event_type IN ({placeholders}) OR (source='recovery' AND "
                              f"event_type='STATE_INCONSISTENCY') ORDER BY id",
                              tuple(sorted(PAPER_BUSINESS_EVENTS))).fetchall()
    for row in rows:
        at, oid = parse_utc(row["timestamp"]), observation_id("paper", row["id"])
        reason = _json(row["payload"], {}).get("reason")
        details = {"run_id": row["run_id"], "symbol": row["symbol"], "operation": row["event_type"],
                   "real_execution": "DISABLED"}
        if row["event_type"] == "STATE_INCONSISTENCY" or reason in PAPER_OPERATIONAL_REJECTIONS:
            health.record_error("paper_broker", provider="paper", at=at, observation_id=oid, details=details,
                                component_error=ComponentErrorType.PAPER_BROKER_ERROR,
                                reason=f"{row['event_type']}: {reason or 'operational failure'}")
        else:
            health.record_success("paper_broker", provider="paper", at=at, observation_id=oid, details=details)


# --- F01-T11 trading database (read-only) and sidecar ---------------------------------------------------------------
def probe_trading_db(path, health, *, now):
    """Opens the trading DB read-only (no migration is possible read-only); records only to the sidecar."""
    oid = observation_id("trading_db", now.isoformat())
    try:
        trading = Store(path, readonly=True)
        try:
            version = trading.db.execute("SELECT version FROM schema_info").fetchone()[0]
            check = trading.db.execute("PRAGMA quick_check").fetchone()[0]
            details = {"schema_version": version, "quick_check": check, "last_run": trading.get_state("last_run")}
        finally:
            trading.close()
    except Exception as exc:  # noqa: BLE001 - observational boundary
        health.record_error("trading_db", at=now, observation_id=oid, component_error=ComponentErrorType.DATABASE_ERROR,
                            reason=f"open failed: {type(exc).__name__}: {exc}")
        return None
    if check != "ok" or version != SCHEMA_VERSION:
        health.record_error("trading_db", at=now, observation_id=oid, details=details,
                            component_error=ComponentErrorType.DATABASE_ERROR,
                            reason="integrity check failed" if check != "ok" else "unexpected schema version")
    else:
        health.record_success("trading_db", at=now, observation_id=oid, details=details)
    return details


def sidecar_status(path):
    """In-memory projection of the sidecar itself; never writes to it (avoids recursive failure)."""
    try:
        store = HealthStore(path, readonly=True)
        store.close()
        return {"component": "health_sidecar", "provider": "", "status": HealthStatus.HEALTHY.value, "reason": None}
    except Exception as exc:  # noqa: BLE001 - observational boundary
        return {"component": "health_sidecar", "provider": "", "status": HealthStatus.FAILED.value,
                "reason": sanitize_reason(f"{type(exc).__name__}: {exc}")}


# --- F01-T12 persistent disk ---------------------------------------------------------------------------------------
def probe_disk(runtime_dir, health, *, now, expected_files=("trading_floor.db",)):
    """Existence, permissions and raw free space. Writes nothing to the runtime directory."""
    path = Path(runtime_dir)
    oid = observation_id("disk", str(path), now.isoformat())
    try:
        if not path.is_dir():
            raise FileNotFoundError("runtime path missing or not a directory")
        usage = shutil.disk_usage(path)
        details = {"readable": os.access(path, os.R_OK), "writable": os.access(path, os.W_OK),
                   "free_bytes": usage.free, "total_bytes": usage.total,
                   "expected_files_present": all((path / name).is_file() for name in expected_files)}
    except Exception as exc:  # noqa: BLE001 - observational boundary
        health.record_error("persistent_disk", at=now, observation_id=oid, component_error=ComponentErrorType.DISK_ERROR,
                            reason=f"{type(exc).__name__}: {exc}")
        return None
    if not (details["readable"] and details["writable"]):
        health.record_error("persistent_disk", at=now, observation_id=oid, details=details, reason="path not accessible",
                            component_error=ComponentErrorType.DISK_ERROR)
    elif not details["expected_files_present"]:
        health.record_state("persistent_disk", at=now, observation_id=oid, status=HealthStatus.DEGRADED,
                            reason="expected runtime files not visible", details=details)
    else:  # No free-space threshold exists: raw evidence only (OWNER DECISION REQUIRED).
        health.record_success("persistent_disk", at=now, observation_id=oid, details=details)
    return details


# --- collection and read-only projection (F01-T14 preparation) -------------------------------------------------------
TRADING_PROBES = (probe_scheduler, probe_market_data, probe_macro_data, probe_ai, probe_risk, probe_paper_broker)


def collect(*, trading_db_path, health_db_path, runtime_dir, policy, now):
    """Run every probe in isolation. Never raises; returns {probe_name: None | sanitized failure}."""
    outcome = {}
    try:
        health = SystemHealth(HealthStore(health_db_path), policy)
    except Exception as exc:  # noqa: BLE001 - a broken sidecar must not reach callers
        return {"health_sidecar": sanitize_reason(f"{type(exc).__name__}: {exc}")}
    try:
        trading = Store(trading_db_path, readonly=True)
    except Exception as exc:  # noqa: BLE001
        trading = None
        outcome["trading_evidence"] = sanitize_reason(f"{type(exc).__name__}: {exc}")
    try:
        for probe in TRADING_PROBES if trading is not None else ():
            outcome[probe.__name__] = _isolated(probe, trading, health)
        outcome["probe_trading_db"] = _isolated(probe_trading_db, trading_db_path, health, now=now)
        outcome["probe_disk"] = _isolated(probe_disk, runtime_dir, health, now=now)
    finally:
        if trading is not None:
            trading.close()
        health.store.close()
    return outcome


def _isolated(probe, *args, **kwargs):
    try:
        probe(*args, **kwargs)
        return None
    except Exception as exc:  # noqa: BLE001 - observational boundary
        return sanitize_reason(f"{type(exc).__name__}: {exc}")


PROJECTION_SCHEMA_VERSION = "v2.system_health_projection.1"


def _envelope(components, now):
    return {"schema_version": PROJECTION_SCHEMA_VERSION, "generated_at": now.isoformat(), "mode": "PAPER",
            "real_execution": "DISABLED", "components": components, "overall_status": None,
            "overall_policy": "OWNER_DECISION_REQUIRED"}


def health_projection(*, health_db_path, policy, now):
    """Stable, sanitized, read-only view for the Batch 3 dashboard. Never writes anything.

    overall_status is deliberately absent: aggregation precedence is an OWNER DECISION.
    """
    components = [sidecar_status(health_db_path), dict(EMAIL_STATUS)]
    try:
        store = HealthStore(health_db_path, readonly=True)
    except Exception:  # noqa: BLE001 - reported by the health_sidecar entry above
        return _envelope(components, now)
    try:
        health = SystemHealth(store, policy)
        for record in health.records():
            view = health.project(record, now=now)
            data = view["record"]
            components.append({
                "component": record.component, "provider": record.provider, "status": view["status"],
                "liveness": view["liveness"], "progress": view["progress"],
                "last_success_at": data["last_success_at"], "last_error_at": data["last_error_at"],
                "last_checked_at": data["last_checked_at"], "heartbeat_at": data["heartbeat_at"],
                "progress_at": data["progress_at"], "progress_stage": data["progress_stage"],
                "consecutive_errors": data["consecutive_errors"], "latency_ms": data["latency_ms"],
                "error_type": data["error_type"], "legacy_error_name": data["legacy_error_name"],
                "reason": data["sanitized_error_reason"], "sanitized_error_reason": data["sanitized_error_reason"],
                "details": data["details"]})
    finally:
        store.close()
    return _envelope(components, now)


__all__ = ["collect", "health_projection", "observation_id", "sidecar_status"]
