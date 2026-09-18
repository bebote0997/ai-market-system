"""Bounded, read-only evidence bundle for private review."""
from datetime import datetime, timezone
import json
import os
import re


MAX_EXPORT_BYTES = 1_000_000
_TOKEN_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}|https://hooks\.slack\.com/services/[^\s\"']+", re.IGNORECASE)
_SECRET_NAMES = ("OPENAI_API_KEY", "TWELVE_DATA_API_KEY", "MASSIVE_API_KEY",
                 "FINNHUB_API_KEY", "EODHD_API_KEY", "SLACK_WEBHOOK_URL",
                 "AI_FLOOR_DASHBOARD_PASSWORD")


def _sanitize(value):
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()
                if not any(part in str(key).lower() for part in ("api_key", "token", "password", "webhook"))}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        result = value
        for name in _SECRET_NAMES:
            secret = os.environ.get(name)
            if secret and len(secret) >= 8:
                result = result.replace(secret, "[REDACTED]")
        return _TOKEN_PATTERN.sub("[REDACTED]", result)[:2000]
    return value


def review_bundle(store, *, limit=100):
    if not 1 <= limit <= 500:
        raise ValueError("invalid review limit")
    rows = store.db.execute("SELECT payload FROM review_reports ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    reviews = []
    for row in rows:
        raw = json.loads(row[0])
        reviews.append({key: raw.get(key) for key in (
            "schema_version", "run_id", "slot_key", "symbol", "as_of", "final_status",
            "setup_status", "agents", "risk_decision", "paper", "warnings", "error",
            "provider_health", "macro_evidence")})
    event_rows = store.db.execute(
        "SELECT payload FROM notification_events ORDER BY journal_id DESC LIMIT ?", (limit,)
    ).fetchall()
    events = [json.loads(row[0]) for row in reversed(event_rows)]
    return _sanitize({"schema_version": "1.0", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "paper_only": True, "real_execution_disabled": True,
            "market_provider": store.get_state("market_provider_mode"),
            "macro_provider_health": store.get_state("macro_provider"),
            "ai_provider_health": store.get_state("ai_provider"),
            "reviews": reviews, "notification_events": events})


def review_bundle_json(store, *, limit=100):
    output = json.dumps(review_bundle(store, limit=limit), ensure_ascii=False, separators=(",", ":"))
    if len(output.encode("utf-8")) > MAX_EXPORT_BYTES:
        raise ValueError("review export exceeds safe size")
    return output
