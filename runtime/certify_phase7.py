"""Offline suite, local doctor and one bounded live no-order infrastructure pass."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from ai.openai_provider import OpenAIProvider
from data.twelve_data_provider import TwelveDataMarketDataProvider
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner, preflight
from runtime.env import load_local_env
from runtime.scheduler import slot_at


class ObservedMarket:
    def __init__(self):
        self.provider = TwelveDataMarketDataProvider(retries=1)
        self.health_by_asset = self.provider.health_by_asset
        self.last_bars = {}

    def load_snapshot(self, symbol, as_of):
        snapshot = self.provider.load_snapshot(symbol, as_of)
        self.last_bars[symbol] = {tf: None if frame.empty else frame.index.max().to_pydatetime().astimezone(timezone.utc)
                                  for tf, frame in snapshot.items()}
        return snapshot


def live_result_passes(result):
    ai = result["ai_statuses"]
    return (result["durable_status"] == "COMPLETED" and result["review_durable"] and
            result["journal_count"] > 0 and result["wall_fresh"] and
            result["status"] not in {"ERROR", "NO_DATA", "STALE_DATA", "SESSION_SKIPPED"} and
            result["paper_order_count"] == 0 and result["openai_health"] == "READY" and
            result["openai_usage_total_tokens"] > 0 and
            all(ai.get(name) in {"OK", "PARTIAL"} for name in
                ("structure_ai", "liquidity_ai", "setup_reviewer_ai")) and
            ai.get("macro_ai") in {"OK", "PARTIAL", "NO_DATA"} and
            ai.get("trade_reviewer_ai", "OK") in {"OK", "PARTIAL"})


def certify(*, env_file, offline=True):
    if offline:
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-q"],
                                cwd=Path(__file__).resolve().parents[1], check=False)
        if result.returncode:
            return {"status": "FAIL", "gate": "OFFLINE_SUITE", "exit_code": result.returncode}
    load_local_env(env_file)
    config = replace(RuntimeConfig(), db_path=Path("data/runtime") / f"phase7-cert-{uuid4().hex}.db",
                     ai_provider_mode="openai", market_provider_mode="twelve_data",
                     scheduler_enabled=False)
    doctor = preflight(config)
    if doctor.status != "READY":
        return {"status": "FAIL", "gate": "PREFLIGHT", "checks": doctor.checks,
                "db_path": str(config.db_path)}
    observed = ObservedMarket()
    runner = DemoRunner(config, market_provider=observed,
                        ai_provider=OpenAIProvider(retries=0), dry_run=True,
                        diagnostic_outside_session=True)
    now = datetime.now(timezone.utc)
    slot = slot_at(now, config.cadence_minutes)
    results = {}
    try:
        for symbol in config.enabled_symbols:
            outcome = runner.run_once(symbol, slot)
            observed_at = datetime.now(timezone.utc)
            review = runner.store.review_report(outcome["run_id"])
            bars = observed.last_bars.get(symbol, {})
            limits = dict(config.max_age_seconds)
            wall_fresh = all(stamp is not None and 0 <= (observed_at - stamp).total_seconds() <= limits[tf]
                             for tf, stamp in bars.items()) and set(bars) == set(limits)
            results[symbol] = {
                "run_id": outcome["run_id"], "status": outcome["status"],
                "durable_status": outcome["durable_status"], "review_durable": outcome["review_durable"],
                "journal_count": outcome["journal_count"], "paper_orders_enabled": outcome["paper_orders_enabled"],
                "last_bars": {tf: {"timestamp_utc": stamp.isoformat(),
                                   "age_seconds": round((observed_at - stamp).total_seconds(), 1),
                                   "limit_seconds": limits[tf]}
                              if stamp else None for tf, stamp in bars.items()},
                "wall_fresh": wall_fresh,
                "ai_statuses": {a["agent"]: a["status"] for a in review["agents"]} if review else {},
                "openai_health": runner.runtime.ai_provider.health,
                "openai_usage_total_tokens": runner.runtime.ai_provider.last_usage.get("total_tokens", 0),
                "paper_order_count": runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0],
            }
    finally:
        runner.close()
    passed = all(live_result_passes(r) for r in results.values())
    return {"status": "PASS" if passed else "FAIL", "gate": "LIVE_DIAGNOSTIC",
            "preflight": doctor.status, "slot_utc": slot.isoformat(),
            "current_utc": now.isoformat(), "provider_health": observed.provider.health_by_asset,
            "results": results, "db_path": str(config.db_path)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--skip-offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = certify(env_file=args.env_file, offline=not args.skip_offline)
    except Exception as exc:
        result = {"status": "FAIL", "gate": "UNHANDLED", "error_type": type(exc).__name__}
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
