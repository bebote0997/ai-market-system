"""Render preparation checks and supervised single-service entry point."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from runtime.config import RuntimeConfig
from runtime.demo_runner import REAL_EXECUTION_ENABLED
from storage.database import Store
from storage.codec import parse_utc


DISK_MOUNT = Path("/opt/render/project/src/data/runtime")


@dataclass(frozen=True)
class CloudPreflight:
    status: str
    checks: dict
    infra_ready: bool
    experiment_ready: bool


def scheduler_healthy(path, now=None, limit_seconds=120):
    now = now or datetime.now(timezone.utc)
    try:
        store = Store(path, readonly=True)
        try:
            heartbeat = store.get_state("heartbeat")
            return bool(heartbeat and store.get_state("scheduler") == "RUNNING" and
                        0 <= (now - parse_utc(heartbeat)).total_seconds() <= limit_seconds)
        finally:
            store.close()
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return False


def cloud_preflight(config, *, env=None, disk_mounted=None):
    env = os.environ if env is None else env
    cloud = env.get("RENDER") == "true"
    path = Path(config.db_path)
    mount = Path(env.get("AI_FLOOR_DURABLE_MOUNT", str(DISK_MOUNT)))
    if disk_mounted is None:
        disk_mounted = os.path.ismount(mount) if cloud else False
    macro_disabled = config.macro_provider_mode == "none"
    macro_certified = (config.macro_provider_mode == "fxmacrodata" and
                       bool(env.get("FXMACRODATA_API_KEY")))
    checks = {
        "paper_only": not REAL_EXECUTION_ENABLED,
        "enabled_symbols": tuple(config.enabled_symbols) == ("XAUUSD", "EURUSD"),
        "market_provider": config.market_provider_mode == "twelve_data",
        "ai_provider": config.ai_provider_mode == "openai" and env.get("OPENAI_MODEL", "gpt-5.6-terra") == "gpt-5.6-terra",
        "macro_provider_disabled": macro_disabled,
        "macro_provider_valid": macro_disabled or macro_certified,
        "macro_provider_certified": macro_certified,
        "scheduler_single_authority": env.get("AI_FLOOR_INSTANCE_COUNT", "1") == "1" and config.cadence_minutes == 15,
        "scheduler_not_started": not config.scheduler_enabled and env.get("AI_FLOOR_CLOUD_RUNNER", "0") == "0",
        "durable_path": path.is_absolute() and (path == mount or mount in path.parents),
        "durable_mount": bool(disk_mounted),
        "dashboard_auth": bool(env.get("AI_FLOOR_DASHBOARD_PASSWORD")),
        "openai_credential": bool(env.get("OPENAI_API_KEY")),
        "twelve_data_credential": bool(env.get("TWELVE_DATA_API_KEY")),
        "slack_configured": bool(env.get("SLACK_WEBHOOK_URL")),
        "db_writable_schema": False,
        "experiment_not_started": False,
    }
    if checks["durable_path"] and checks["durable_mount"]:
        try:
            store = Store(path)
            try:
                checks["db_writable_schema"] = store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                if checks["db_writable_schema"]:
                    with store.transaction():
                        store.db.execute("CREATE TEMP TABLE IF NOT EXISTS cloud_write_probe(value INTEGER)")
                        store.db.execute("INSERT INTO cloud_write_probe VALUES(1)")
                        store.db.execute("DELETE FROM cloud_write_probe")
                checks["experiment_not_started"] = store.get_state("experiment_started") != "1"
            finally:
                store.close()
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            checks["db_writable_schema"] = False
    infrastructure = (
        "paper_only", "enabled_symbols", "market_provider", "ai_provider",
        "macro_provider_valid", "scheduler_single_authority", "scheduler_not_started",
        "durable_path", "durable_mount", "dashboard_auth", "db_writable_schema",
        "experiment_not_started",
    )
    infra_ready = all(checks[name] for name in infrastructure)
    experiment_ready = infra_ready and all(checks[name] for name in (
        "macro_provider_certified", "openai_credential", "twelve_data_credential",
        "slack_configured",
    ))
    return CloudPreflight("INFRA_READY" if infra_ready else "NOT_READY", checks,
                          infra_ready, experiment_ready)


def serve():
    """Start read-only web; optionally supervise one runner after future activation."""
    if os.environ.get("RENDER") != "true":
        raise RuntimeError("cloud entry point requires Render")
    port = os.environ.get("PORT", "10000")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("invalid PORT")
    if not os.environ.get("AI_FLOOR_DASHBOARD_PASSWORD"):
        raise RuntimeError("dashboard access not configured")
    children = []
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    active = os.environ.get("AI_FLOOR_CLOUD_RUNNER", "0") == "1"
    if active:
        config = RuntimeConfig.from_env()
        if not config.scheduler_enabled:
            raise RuntimeError("runner requested with scheduler disabled")
        # Activation is intentionally separate from preparation and requires a
        # mounted disk, credentials, and a single configured service instance.
        activation_env = dict(os.environ, AI_FLOOR_CLOUD_RUNNER="0", AI_FLOOR_SCHEDULER="0")
        from dataclasses import replace
        doctor = cloud_preflight(replace(config, scheduler_enabled=False), env=activation_env)
        if not doctor.experiment_ready:
            raise RuntimeError("experiment activation preflight not ready")
        children.append(subprocess.Popen([sys.executable, "-m", "runtime.cloud_runner"]))
    else:
        doctor = cloud_preflight(RuntimeConfig.from_env())
        if not doctor.infra_ready:
            raise RuntimeError("cloud infrastructure preflight not ready")
    children.append(subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", "ui/app.py", "--server.headless=true",
        "--server.address=0.0.0.0", f"--server.port={port}"]))
    started = time.monotonic()
    last_health_check = 0.0
    try:
        while not stop:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("cloud child process stopped")
            elapsed = time.monotonic() - started
            if active and elapsed > 120 and elapsed - last_health_check >= 30:
                last_health_check = elapsed
                if not scheduler_healthy(config.db_path):
                    raise RuntimeError("scheduler heartbeat stale")
            time.sleep(1)
    finally:
        shutdown()
        for child in children:
            try:
                child.wait(timeout=90)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    if "--preflight" in sys.argv:
        result = cloud_preflight(RuntimeConfig.from_env())
        print(json.dumps({"status": result.status, "infra_ready": result.infra_ready,
                          "experiment_ready": result.experiment_ready,
                          "checks": result.checks}, sort_keys=True))
        raise SystemExit(0 if result.infra_ready else 1)
    serve()
