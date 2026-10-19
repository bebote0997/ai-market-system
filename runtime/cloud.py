"""Render preparation checks and supervised single-service entry point."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

from runtime.config import RuntimeConfig, catch_up_scope_checks, catch_up_storage_checks, persistent_write_probe
from runtime.demo_runner import REAL_EXECUTION_ENABLED
from storage.database import Store
from storage.codec import parse_utc
from ui.auth import MIN_PASSWORD_LENGTH, password_strong_enough, session_ttl_seconds


DISK_MOUNT = Path("/opt/render/project/src/data/runtime")
WATCHDOG_READ_RETRIES = 3  # an unreadable DB (transient lock) is re-read before the service is restarted
WATCHDOG_RETRY_SECONDS = 2
_sleep = time.sleep  # indirection so tests never really sleep
LOGGER = logging.getLogger(__name__)
ARCHIVE_DIR, ARCHIVE_SUFFIX = "archive", "_pre_demo_20261018"
ARCHIVE_MARGIN_BYTES = 64 * 1024 * 1024
SQLITE_HEADER = b"SQLite format 3\x00"
WAL_MAGIC = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")


@dataclass(frozen=True)
class CloudPreflight:
    status: str
    checks: dict
    infra_ready: bool
    experiment_ready: bool


def scheduler_health_detail(path, now=None, limit_seconds=120):
    """Watchdog view of the heartbeat; ``error_type`` is set only when the DB could not be read."""
    now = now or datetime.now(timezone.utc)
    detail = {"healthy": False, "scheduler": None, "heartbeat_age_seconds": None, "error_type": None}
    try:
        store = Store(path, readonly=True)
        try:
            heartbeat = store.get_state("heartbeat")
            detail["scheduler"] = store.get_state("scheduler")
            if heartbeat:
                age = (now - parse_utc(heartbeat)).total_seconds()
                detail["heartbeat_age_seconds"] = int(age)
                detail["healthy"] = detail["scheduler"] == "RUNNING" and 0 <= age <= limit_seconds
        finally:
            store.close()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        detail.update(healthy=False, error_type=type(exc).__name__)
    return detail


def scheduler_healthy(path, now=None, limit_seconds=120):
    return scheduler_health_detail(path, now=now, limit_seconds=limit_seconds)["healthy"]


def check_scheduler_or_raise(path):
    """Watchdog step: re-read an unreadable DB, never a readable stale heartbeat; log the evidence, then raise."""
    detail = scheduler_health_detail(path)
    for _ in range(WATCHDOG_READ_RETRIES):
        if detail["healthy"] or detail["error_type"] is None:
            break
        _sleep(WATCHDOG_RETRY_SECONDS)
        detail = scheduler_health_detail(path)
    if detail["healthy"]:
        return detail
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    LOGGER.error("component=watchdog event=SCHEDULER_STALE scheduler=%s heartbeat_age_s=%s error_type=%s",
                 detail["scheduler"], detail["heartbeat_age_seconds"], detail["error_type"])
    raise RuntimeError("scheduler heartbeat stale")


def _archive_log(event, **fields):
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    LOGGER.info("component=archive event=%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))


def archive_pre_demo_db(db_path, *, disk_usage=shutil.disk_usage):
    """One-time copy of the PAPER trading DB taken before the OANDA demo: ``<db dir>/archive/<stem>_pre_demo_20261018.db``
    via the SQLite backup API from a ``mode=ro`` source, written to a temp file in the archive dir then ``os.replace``.
    Only when the source exists, the target does not, and free space >= 2*size + 64 MiB. Never deletes the source,
    never raises; logs one line (codes and byte counts only, no paths). Returns the event name."""
    tmp = None
    try:
        source = Path(db_path)
        if not source.is_file():
            _archive_log("PRE_DEMO_ARCHIVE_SKIPPED", reason="missing_source")
            return "PRE_DEMO_ARCHIVE_SKIPPED"
        folder = source.parent / ARCHIVE_DIR
        target = folder / f"{source.stem}{ARCHIVE_SUFFIX}.db"
        if target.exists():
            _archive_log("PRE_DEMO_ARCHIVE_SKIPPED", reason="exists")
            return "PRE_DEMO_ARCHIVE_SKIPPED"
        wal = source.with_name(source.name + "-wal")
        with open(source, "rb") as handle:
            header_ok = handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        if header_ok and wal.is_file() and wal.stat().st_size:
            with open(wal, "rb") as handle:  # SQLite would discard an unrecognised WAL: never let it touch one
                header_ok = handle.read(4) in WAL_MAGIC
        if not header_ok:
            _archive_log("PRE_DEMO_ARCHIVE_SKIPPED", reason="not_sqlite")
            return "PRE_DEMO_ARCHIVE_SKIPPED"
        size = source.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
        if disk_usage(source.parent).free < 2 * size + ARCHIVE_MARGIN_BYTES:
            _archive_log("PRE_DEMO_ARCHIVE_SKIPPED", reason="low_space")
            return "PRE_DEMO_ARCHIVE_SKIPPED"
        folder.mkdir(mode=0o700, exist_ok=True)
        tmp = folder / f".{target.name}.{os.getpid()}.tmp"
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                dst.execute("PRAGMA journal_mode=DELETE")  # the archive is one self-contained file
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        tmp = None
        _archive_log("PRE_DEMO_ARCHIVED", bytes=target.stat().st_size)
        return "PRE_DEMO_ARCHIVED"
    except Exception as exc:  # noqa: BLE001 - the archive is best effort and never blocks startup
        try:
            _archive_log("PRE_DEMO_ARCHIVE_SKIPPED", reason="error", error_type=type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass
        return "PRE_DEMO_ARCHIVE_SKIPPED"
    finally:
        if tmp is not None:
            try:
                Path(tmp).unlink(missing_ok=True)  # only our own partial temp copy, never the source
            except Exception:  # noqa: BLE001
                pass


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
        "dashboard_auth": password_strong_enough(env.get("AI_FLOOR_DASHBOARD_PASSWORD")),
        "openai_credential": bool(env.get("OPENAI_API_KEY")),
        "twelve_data_credential": bool(env.get("TWELVE_DATA_API_KEY")),
        "slack_configured": bool(env.get("SLACK_WEBHOOK_URL")),
        "db_writable_schema": False,
        "experiment_not_started": False,
        "experiment_resumable": False,
    }
    from runtime.genesis import sealed_preflight_checks
    sealed_checks = sealed_preflight_checks(config, env)  # V2 P2b (R-GEN-1d, D-3): {} while OFF; before any DB open
    checks.update(sealed_checks)
    sealed_blocked = bool(sealed_checks) and (not sealed_checks["sealed_genesis_context"] or not path.exists())
    if checks["durable_path"] and checks["durable_mount"] and not sealed_blocked:
        try:
            if sealed_checks:  # LOW-1: non-creating open (mode=rw)
                from runtime.genesis import SealedStore
                store = SealedStore(path)
            else:
                store = Store(path)
            try:
                checks["db_writable_schema"] = store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                # P8.4G (DEC-8.11, P8.5R HIGH): a TEMP table proved nothing about the file; probe a persistent write
                # that is always rolled back (no data, schema or schema-version change).
                checks["db_writable_schema"] = checks["db_writable_schema"] and persistent_write_probe(store.db, path)
                checks["experiment_not_started"] = store.get_state("experiment_started") != "1"
                if not checks["experiment_not_started"]:
                    # A restart must retain the experiment identity and ledger.
                    # Activation callers temporarily disable the scheduler for
                    # preflight; durable run metadata describes the active config.
                    freeze = env.get("AI_FLOOR_GIT_COMMIT")
                    fingerprint = replace(config, scheduler_enabled=True).fingerprint()
                    metadata = store.db.execute(
                        "SELECT DISTINCT git_commit, config_fingerprint, starting_equity FROM run_metadata"
                    ).fetchall()
                    checks["experiment_resumable"] = bool(
                        freeze and store.get_state("experiment_freeze_sha") == freeze
                        and store.get_state("experiment_started_at_utc")
                        and store.db.execute("SELECT 1 FROM paper_accounts WHERE account_id=?",
                                             (config.account_id,)).fetchone()
                        and all(tuple(row) == (freeze, fingerprint, config.starting_equity)
                                for row in metadata)
                    )
            finally:
                store.close()
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            checks["db_writable_schema"] = False
    checks["experiment_state_compatible"] = (
        checks["experiment_not_started"] or checks["experiment_resumable"])
    catch_up_checks = {**catch_up_storage_checks(config, mount=mount, disk_mounted=disk_mounted),
                       **catch_up_scope_checks(config)}  # P1-B: scope and STRICT evidence; {} while OFF
    checks.update(catch_up_checks)  # empty while the catch-up flag is OFF: the OFF report is unchanged
    infrastructure = (
        "paper_only", "enabled_symbols", "market_provider", "ai_provider",
        "macro_provider_valid", "scheduler_single_authority", "scheduler_not_started",
        "durable_path", "durable_mount", "dashboard_auth", "db_writable_schema",
        "experiment_state_compatible", *catch_up_checks, *sealed_checks,
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
    try:
        from execution.log_redaction import install_log_redaction
        install_log_redaction()
    except Exception:  # noqa: BLE001 - log hardening never blocks startup
        pass
    if os.environ.get("RENDER") != "true":
        raise RuntimeError("cloud entry point requires Render")
    port = os.environ.get("PORT", "10000")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("invalid PORT")
    if not os.environ.get("AI_FLOOR_DASHBOARD_PASSWORD"):
        raise RuntimeError("dashboard access not configured")
    if not password_strong_enough(os.environ.get("AI_FLOOR_DASHBOARD_PASSWORD")):
        raise RuntimeError(f"AI_FLOOR_DASHBOARD_PASSWORD must have at least {MIN_PASSWORD_LENGTH} characters")
    session_ttl_seconds()  # invalid AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS fails startup, not the first login
    # Owner-only files for everything created from here on (trading DB, candle cache, ledgers), inherited by children.
    os.umask(0o077)
    try:  # Existing files keep their old mode: tighten the trading DB (and sidecars) and the broker state DB.
        from execution.fs_security import secure_sqlite_files
        secure_sqlite_files(RuntimeConfig.from_env().db_path)
        secure_sqlite_files(os.environ.get("AI_FLOOR_BROKER_STATE_DB_PATH") or "data/runtime/broker_state.db")
    except Exception:  # noqa: BLE001 - hardening never blocks startup
        pass
    try:  # Best-effort one-time archive of the PAPER trading DB before the OANDA demo (never deletes, never raises).
        archive_pre_demo_db(RuntimeConfig.from_env().db_path)
    except Exception:  # noqa: BLE001 - the archive never blocks startup
        pass
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
    # Default entry adds HSTS/CSP/X-Frame-Options/nosniff/Referrer-Policy (ui/security_headers.py via st.App).
    # AI_FLOOR_SECURITY_HEADERS=0 is the rollback lever: plain ``ui/app.py`` without the middleware.
    entry = "ui/app.py" if os.environ.get("AI_FLOOR_SECURITY_HEADERS") == "0" else "ui/asgi_app.py"
    children.append(subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", entry, "--server.headless=true",
        "--server.address=0.0.0.0", f"--server.port={port}",
        # Never show tracebacks or exception messages to remote visitors.
        "--client.showErrorDetails=none",
        # Explicit hardening (do not rely on Streamlit defaults); viewer toolbar hides the developer menu.
        "--server.enableXsrfProtection=true", "--server.enableCORS=true", "--browser.gatherUsageStats=false",
        "--server.enableStaticServing=false", "--client.toolbarMode=viewer"]))
    started = time.monotonic()
    last_health_check = 0.0
    try:
        while not stop:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("cloud child process stopped")
            elapsed = time.monotonic() - started
            if active and elapsed > 120 and elapsed - last_health_check >= 30:
                last_health_check = elapsed
                check_scheduler_or_raise(config.db_path)
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
