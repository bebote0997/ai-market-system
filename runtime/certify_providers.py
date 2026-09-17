"""Opt-in live capability check. Never creates a paper order or strategy signal."""
import argparse
from datetime import datetime, timezone
import json
import os

from ai.contracts import AIRequest, validate_ai_response
from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from data.massive_provider import MassiveMarketDataProvider, MassiveProviderError, SYMBOLS, TIMEFRAMES
from data.twelve_data_provider import (TwelveDataMarketDataProvider, TwelveDataProviderError,
                                       SYMBOLS as TWELVE_SYMBOLS, TIMEFRAMES as TWELVE_TIMEFRAMES)
from runtime.env import load_local_env
from runtime.config import RuntimeConfig, SUPPORTED_SYMBOLS
from runtime.gates import fresh_snapshot


def certify_openai():
    provider = OpenAIProvider(timeout=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "30")), retries=0)
    result = {"provider": "openai", "model": provider.model, "status": "NOT_CONFIGURED"}
    if not provider.api_key:
        return result
    now = datetime.now(timezone.utc)
    request = AIRequest(
        "1.0", "certification-only", now, "XAUUSD", "structure_ai",
        "structure_specialist", {}, ({"evidence_id": "cert_evidence", "bias": "UNKNOWN"},),
        ("interpret_structure",), {"no_execution": True}, "1.0",
    )
    try:
        response = provider.generate(request)
        valid, reason = validate_ai_response(response, request)
        result["response_status"] = response.status
        usage = provider.last_usage
        usage_valid = (isinstance(usage, dict) and
                       isinstance(usage.get("total_tokens"), int) and usage["total_tokens"] > 0)
        result["status"] = ("PASS" if valid and usage_valid and
                            response.status in {"OK", "PARTIAL", "NO_DATA"} else "FAIL")
        result["contract"] = reason
        result["usage"] = usage
    except OpenAIProviderError as exc:
        result["status"] = "FAIL"
        result["reason"] = exc.kind
        result["http_status"] = exc.http_status
        result["error_type"] = exc.error_type
        result["error_code"] = exc.error_code
        result["retry_after_seconds"] = exc.retry_after
    return result


def _fx_market_state(provider, now):
    """Use Massive's current FX status only when its server time is current."""
    try:
        data = provider._get("https://api.massive.com/v1/marketstatus/now", "FOREX")
        fx = data.get("currencies", {}).get("fx") if isinstance(data, dict) else None
        raw_time = data.get("serverTime") if isinstance(data, dict) else None
        server_time = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) else None
        if server_time is None or server_time.tzinfo is None or abs((now - server_time).total_seconds()) > 120:
            return "UNKNOWN"
        return {"open": "OPEN", "closed": "CLOSED"}.get(fx, "UNKNOWN")
    except (MassiveProviderError, ValueError, TypeError):
        return "UNKNOWN"


def certify_massive(enabled_symbols):
    provider = MassiveMarketDataProvider(timeout=float(os.environ.get("MASSIVE_TIMEOUT_SECONDS", "15")),
                                         retries=1)
    report = {"provider": "massive", "status": "NOT_CONFIGURED",
              "supported_symbols": SUPPORTED_SYMBOLS,
              "enabled_symbols": enabled_symbols, "symbols": {},
              "fx_market_status": "NOT_QUERIED"}
    now = datetime.now(timezone.utc)
    limits = dict(RuntimeConfig().max_age_seconds)
    rate_limited = False
    for symbol, (ticker, asset_class) in SYMBOLS.items():
        item = {"provider_symbol": ticker, "asset_class": asset_class,
                "supported": True, "enabled": symbol in enabled_symbols,
                "certified": False, "reference": "NOT_TESTED",
                "timeframes": {tf: {"status": "NOT_TESTED"} for tf in TIMEFRAMES},
                "freshness": "NOT_TESTED", "certification_utc": now.isoformat()}
        report["symbols"][symbol] = item
        if symbol not in enabled_symbols:
            item["status"] = "NOT_ENABLED"
            continue
        if not provider.api_key:
            item["status"] = "NOT_CONFIGURED"
            continue
        if rate_limited:
            item["status"] = "PARTIAL"
            item["reason"] = "NOT_TESTED_RATE_LIMIT"
            continue
        snapshot = {}
        for timeframe in ("5m", "15m", "1h"):
            detail = item["timeframes"][timeframe]
            detail["freshness_threshold_seconds"] = limits[timeframe]
            try:
                frame = provider._bars(symbol, timeframe, now)
            except MassiveProviderError as exc:
                detail["status"] = exc.kind
                detail["http_status"] = exc.http_status
                detail["retry_after_seconds"] = exc.retry_after
                item["reason"] = exc.kind
                if exc.kind == "RATE_LIMITED":
                    rate_limited = True
                break
            if frame.empty:
                detail["status"] = "EMPTY"
                item["reason"] = "EMPTY"
                break
            snapshot[timeframe] = frame
            item["reference"] = "VERIFIED_BY_OHLC_TICKER"
            latest = frame.index.max().to_pydatetime().astimezone(timezone.utc)
            detail["latest_bar_utc"] = latest.isoformat()
            detail["last_closed_utc"] = (latest + TIMEFRAMES[timeframe][2]).isoformat()
            detail["certification_utc"] = now.isoformat()
            detail["age_seconds"] = round((now - latest).total_seconds(), 3)
            detail["status"] = "PASS" if detail["age_seconds"] <= limits[timeframe] else "STALE_DATA"
        if len(snapshot) == len(TIMEFRAMES):
            stale = any(d["status"] == "STALE_DATA" for d in item["timeframes"].values())
            if stale and report["fx_market_status"] == "NOT_QUERIED":
                report["fx_market_status"] = _fx_market_state(provider, now)
            if symbol in provider.delayed_symbols:
                for detail in item["timeframes"].values():
                    if detail["status"] == "PASS":
                        detail["status"] = "STALE_DATA"
            if stale:
                market = report["fx_market_status"]
                for detail in item["timeframes"].values():
                    if detail["status"] == "STALE_DATA":
                        detail["status"] = ("MARKET_CLOSED" if market == "CLOSED" else
                                            "STALE_DATA" if market == "OPEN" and symbol == "EURUSD"
                                            else "MARKET_STATUS_UNKNOWN")
            fresh, state, _ = fresh_snapshot(snapshot, symbol, now, RuntimeConfig().max_age_seconds)
            if not fresh and state != "STALE_DATA":
                item["reason"] = state
            statuses = {detail["status"] for detail in item["timeframes"].values()}
            item["freshness"] = "PASS" if statuses == {"PASS"} and fresh else (
                "MARKET_CLOSED" if "MARKET_CLOSED" in statuses else
                "MARKET_STATUS_UNKNOWN" if "MARKET_STATUS_UNKNOWN" in statuses else
                "STALE_DATA" if "STALE_DATA" in statuses else state)
            item["certified"] = item["reference"] == "VERIFIED_BY_OHLC_TICKER" and item["freshness"] == "PASS"
        if not item["certified"]:
            item["status"] = "PARTIAL"
        else:
            item["status"] = "PASS"
    report["health_by_asset"] = provider.health_by_asset
    report["status"] = ("PASS" if all(report["symbols"][s]["certified"] for s in enabled_symbols)
                        else "PARTIAL" if provider.api_key else "NOT_CONFIGURED")
    return report


def certify_twelve_data(enabled_symbols):
    """Certify only active symbols with one request per required interval."""
    provider = TwelveDataMarketDataProvider(
        timeout=float(os.environ.get("TWELVE_DATA_TIMEOUT_SECONDS", "15")), retries=1)
    now = datetime.now(timezone.utc)
    limits = dict(RuntimeConfig().max_age_seconds)
    report = {"provider": "twelve_data", "status": "NOT_CONFIGURED",
              "supported_symbols": SUPPORTED_SYMBOLS, "enabled_symbols": enabled_symbols,
              "symbols": {}, "certification_utc": now.isoformat()}
    rate_limited = False
    for symbol in SUPPORTED_SYMBOLS:
        mapping = TWELVE_SYMBOLS.get(symbol)
        item = {"supported": True, "enabled": symbol in enabled_symbols,
                "certified": False, "provider_symbol": mapping[0] if mapping else None,
                "timeframes": {tf: {"status": "NOT_TESTED"} for tf in TWELVE_TIMEFRAMES},
                "freshness": "NOT_TESTED"}
        report["symbols"][symbol] = item
        if symbol not in enabled_symbols:
            item["status"] = "NOT_ENABLED"
            continue
        if mapping is None:
            item.update(status="UNSUPPORTED", reason="NO_TWELVE_DATA_MAPPING")
            continue
        if not provider.api_key:
            item["status"] = "NOT_CONFIGURED"
            continue
        if rate_limited:
            item.update(status="PARTIAL", reason="NOT_TESTED_RATE_LIMIT")
            continue
        snapshot = {}
        for tf in ("5m", "15m", "1h"):
            detail = item["timeframes"][tf]
            detail.update(provider_symbol=mapping[0], timeframe=tf,
                          certification_utc=now.isoformat(),
                          freshness_threshold_seconds=limits[tf], market_status="UNKNOWN")
            try:
                frame = provider._bars(symbol, tf, now)
            except TwelveDataProviderError as exc:
                detail.update(status=exc.kind, http_status=exc.http_status,
                              retry_after_seconds=exc.retry_after)
                item["reason"] = exc.kind
                if exc.kind == "RATE_LIMITED":
                    rate_limited = True
                break
            if frame.empty:
                detail["status"] = "EMPTY"
                item["reason"] = "EMPTY"
                break
            snapshot[tf] = frame
            latest = frame.index.max().to_pydatetime().astimezone(timezone.utc)
            age = (now - latest).total_seconds()
            detail.update(latest_bar_utc=latest.isoformat(),
                          last_closed_utc=(latest + TWELVE_TIMEFRAMES[tf][1]).isoformat(),
                          age_seconds=round(age, 3),
                          status="PASS" if 0 <= age <= limits[tf] else "STALE_DATA")
        if len(snapshot) == len(TWELVE_TIMEFRAMES):
            fresh, state, _ = fresh_snapshot(snapshot, symbol, now, RuntimeConfig().max_age_seconds)
            statuses = {d["status"] for d in item["timeframes"].values()}
            item["freshness"] = "PASS" if fresh and statuses == {"PASS"} else (
                "STALE_DATA" if "STALE_DATA" in statuses else state)
            item["certified"] = item["freshness"] == "PASS"
        item["status"] = "PASS" if item["certified"] else "PARTIAL"
    report["health_by_asset"] = provider.health_by_asset
    report["status"] = ("PASS" if all(report["symbols"][s]["certified"] for s in enabled_symbols)
                        else "PARTIAL" if provider.api_key else "NOT_CONFIGURED")
    return report


def certify(env_file=".env.local", only="all"):
    load_local_env(env_file)
    config = RuntimeConfig.from_env()
    result = {"supported_symbols": SUPPORTED_SYMBOLS,
              "enabled_symbols": config.enabled_symbols, "scope": only}
    if only == "massive":
        result["massive"] = certify_massive(config.enabled_symbols)
    else:
        result["massive"] = {"provider": "massive", "status": "NOT_ACTIVE",
                             "supported_symbols": SUPPORTED_SYMBOLS}
    if only in {"all", "openai"}:
        result["openai"] = certify_openai()
    if only in {"all", "twelve_data"}:
        result["twelve_data"] = certify_twelve_data(config.enabled_symbols)
    active_names = (("massive",) if only == "massive" else
                    ("openai",) if only == "openai" else
                    ("twelve_data",) if only == "twelve_data" else
                    ("openai", "twelve_data"))
    statuses = {result[name]["status"] for name in active_names}
    result["overall"] = "PASS" if statuses == {"PASS"} else "PARTIAL" if "PASS" in statuses or "PARTIAL" in statuses or "NOT_CONFIGURED" in statuses else "FAIL"
    return result


def main():
    parser = argparse.ArgumentParser(description="PAPER provider capability check; no trading")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    parser.add_argument("--env-file", default=".env.local",
                        help="local env file to load without printing secret values")
    parser.add_argument("--only", choices=("all", "twelve_data", "massive", "openai"), default="all",
                        help="certify one provider without contacting the other")
    args = parser.parse_args()
    result = certify(args.env_file, args.only)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for name in ("openai", "twelve_data", "massive"):
            if name not in result:
                continue
            print(f"{name.upper()}: {result[name]['status']}")
            if name in {"massive", "twelve_data"} and "symbols" in result[name]:
                for symbol, item in result[name]["symbols"].items():
                    print(f"  {symbol} {item['provider_symbol']}: {item['status']} "
                          f"reference={item['reference']} timeframes={item['timeframes']} "
                          f"freshness={item['freshness']} reason={item.get('reason', '-')}")
        print(f"OVERALL: {result['overall']}")
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
