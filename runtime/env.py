"""Load only approved local configuration names without exposing their values."""
import os
from pathlib import Path


NAMES = {"OPENAI_API_KEY", "OPENAI_MODEL", "MASSIVE_API_KEY", "TWELVE_DATA_API_KEY",
         "FINNHUB_API_KEY", "SLACK_WEBHOOK_URL", "AI_FLOOR_DASHBOARD_PASSWORD",
         "AI_FLOOR_MARKET_PROVIDER", "AI_FLOOR_AI_PROVIDER",
         "AI_FLOOR_MACRO_PROVIDER",
         "OPENAI_TIMEOUT_SECONDS", "MASSIVE_TIMEOUT_SECONDS", "TWELVE_DATA_TIMEOUT_SECONDS",
         "AI_FLOOR_ENABLED_SYMBOLS"}


def load_local_env(path=".env.local"):
    target = Path(path)
    if target.is_symlink():
        raise ValueError("env file cannot be a symlink")
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in NAMES:
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))
