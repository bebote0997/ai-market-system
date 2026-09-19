"""Explicit cloud experiment startup. Preflight is read-only and never starts a runner."""
import argparse
import json
import os
import re

from runtime.config import RuntimeConfig
from runtime.scheduler import slot_at
from runtime.service import OperationalRuntime


SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")


def preflight(environ=None):
    environ = os.environ if environ is None else environ
    return {
        "authorized": environ.get("AI_FLOOR_EXPERIMENT_AUTHORIZED") == "1",
        "baseline_sha_present": bool(SHA_PATTERN.fullmatch(environ.get("AI_FLOOR_GIT_COMMIT", ""))),
        "scheduler_enabled": RuntimeConfig.from_env().scheduler_enabled,
    }


def start_authorized_cycle(runtime, symbol, scheduled_at, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("AI_FLOOR_EXPERIMENT_AUTHORIZED") != "1":
        raise PermissionError("cloud experiment start is not authorized")
    baseline_sha = environ.get("AI_FLOOR_GIT_COMMIT", "")
    if not SHA_PATTERN.fullmatch(baseline_sha):
        raise ValueError("AI_FLOOR_GIT_COMMIT must be a SHA for experiment startup")
    return runtime.run_cycle(symbol, scheduled_at, experiment_baseline_sha=baseline_sha)


def main():
    parser = argparse.ArgumentParser(description="Authorized one-cycle cloud experiment runner")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--once", choices=("XAUUSD", "NAS100", "EURUSD"))
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(), sort_keys=True))
        return
    config = RuntimeConfig.from_env()
    symbol = args.once or config.enabled_symbols[0]
    if symbol not in config.enabled_symbols:
        parser.error(f"{symbol} is supported but disabled by AI_FLOOR_ENABLED_SYMBOLS")
    runtime = OperationalRuntime(config)
    try:
        print(start_authorized_cycle(runtime, symbol, slot_at(runtime.clock(), config.cadence_minutes)))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()