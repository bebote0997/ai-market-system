"""Broker environment configuration (SKELETON, not wired). Safe defaults: everything OFF.

Values are read from an injected mapping (``os.environ`` when omitted). ``AI_FLOOR_BROKER_REAL_ENABLED`` is read only
so its presence can be reported: it is NEVER honoured (``real_enabled`` is always False). REAL_EXECUTION_ENABLED stays
the literal False constant in runtime/demo_runner.py and is not an environment variable.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_BASE_URL = "https://api.liquidcharts.com/dxsca-web/"
DEFAULT_STATE_DB_PATH = Path("data/runtime/broker_state.db")
EXPERIMENT_DB_DEFAULT = Path("data/runtime/trading_floor.db")
_ON = {"1", "true", "yes", "on"}
_OFF = {"", "0", "false", "no", "off"}
DEMO_PROVIDERS = ("dxtrade", "oanda")
DEFAULT_DEMO_PROVIDER = "dxtrade"


def mask_account(code):
    """``DEM_2775212_1`` -> ``DEM_…12_1``. Empty -> ``""``."""
    code = str(code or "")
    if not code:
        return ""
    prefix = code.split("_", 1)[0] + "_" if "_" in code else ""
    return f"{prefix}…{code[-4:]}" if len(code) > len(prefix) + 4 else f"{prefix}…"


def base_url_problem(url):
    """None when ``url`` is an acceptable DXtrade base: https, a host, and no userinfo/query/fragment."""
    url = url if isinstance(url, str) else ""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        return "base_url is not a valid URL"
    if parts.scheme != "https" or not parts.hostname:
        return "base_url must be an https URL with a host"
    if "@" in parts.netloc:
        return "base_url must not contain userinfo"
    if "?" in url or "#" in url:
        return "base_url must not contain a query or fragment"
    return None


def display_base_url(url):
    """Sanitised endpoint for repr/logs: scheme://host[:port]/path, never userinfo, query or fragment."""
    try:
        parts = urlsplit(url if isinstance(url, str) else "")
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "<invalid>"
    if not parts.scheme or not host:
        return "<invalid>"
    return f"{parts.scheme}://{host}{port}{parts.path}"


def demo_flag(raw):
    """Only the exact value ``1`` enables the demo adapter."""
    return str(raw if raw is not None else "0").strip() == "1"


def emergency_flag(raw):
    """Unset/0/false/no/off -> inactive; 1/true/yes/on -> active; anything else -> active (fail closed)."""
    value = str(raw if raw is not None else "0").strip().lower()
    return value not in _OFF


def demo_provider(raw):
    """``AI_FLOOR_BROKER_DEMO_PROVIDER``: only ``dxtrade``/``oanda``; unset or anything else -> ``dxtrade``.

    The OANDA adapter requires exactly ``oanda``, so a typo never enables it."""
    value = str(raw if raw is not None else "").strip().lower()
    return value if value in DEMO_PROVIDERS else DEFAULT_DEMO_PROVIDER


@dataclass(frozen=True)
class BrokerConfig:
    demo_enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    domain: str = field(default="", repr=False)
    account_code: str = field(default="", repr=False)
    emergency_stop: bool = False
    state_db_path: Path = DEFAULT_STATE_DB_PATH
    experiment_db_path: Path = EXPERIMENT_DB_DEFAULT
    real_requested: bool = False  # Raw AI_FLOOR_BROKER_REAL_ENABLED, informational only.
    demo_provider: str = DEFAULT_DEMO_PROVIDER

    @property
    def real_enabled(self):
        return False  # Never honoured in this skeleton, whatever the environment says.

    @property
    def account_masked(self):
        return mask_account(self.account_code)

    def __repr__(self):
        return (f"BrokerConfig(demo_enabled={self.demo_enabled}, base_url={display_base_url(self.base_url)!r}, "
                f"username={'***' if self.username else ''!r}, password={'***' if self.password else ''!r}, "
                f"domain={'***' if self.domain else ''!r}, account={self.account_masked!r}, emergency_stop={self.emergency_stop}, "
                f"state_db_path={str(self.state_db_path)!r}, demo_provider={self.demo_provider!r}, "
                f"real_enabled=False)")

    __str__ = __repr__


def load_broker_config(env=None):
    env = os.environ if env is None else env
    return BrokerConfig(
        demo_enabled=demo_flag(env.get("AI_FLOOR_BROKER_DEMO_ENABLED")),
        base_url=(env.get("DXTRADE_BASE_URL") or "").strip() or DEFAULT_BASE_URL,
        username=env.get("DXTRADE_USERNAME") or "",
        password=env.get("DXTRADE_PASSWORD") or "",
        domain=(env.get("DXTRADE_DOMAIN") or "").strip(),
        account_code=(env.get("DXTRADE_ACCOUNT_CODE") or "").strip(),
        emergency_stop=emergency_flag(env.get("AI_FLOOR_EMERGENCY_STOP")),
        state_db_path=Path((env.get("AI_FLOOR_BROKER_STATE_DB_PATH") or "").strip() or DEFAULT_STATE_DB_PATH),
        experiment_db_path=Path((env.get("AI_FLOOR_DB_PATH") or "").strip() or EXPERIMENT_DB_DEFAULT),
        real_requested=(env.get("AI_FLOOR_BROKER_REAL_ENABLED") or "").strip().lower() in _ON,
        demo_provider=demo_provider(env.get("AI_FLOOR_BROKER_DEMO_PROVIDER")),
    )
