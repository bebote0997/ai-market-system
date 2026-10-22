"""Which results the owner sees (DEC-OANDA-ONLY-1): display only, never trading.

``AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`` (exact) = OANDA-demo-only mode: the dashboard (Overview / Trades / Decisions /
Command Center / luxury Floor), the animated Floor page and the daily Slack summary show ONLY the OANDA practice demo
results, even while broker-demo routing is OFF (then "sin datos", never PAPER numbers); the PAPER ledger / P&L sections
are hidden, and so are PAPER fill / close journal events and P&L keys in History, Errors, the Process view (its entry /
exit / outcome steps show the OANDA demo rows of the same run, or NO_DATA) and the Floor journal feed. Unset or any other value = current behaviour (OANDA results while ``AI_FLOOR_BROKER_DEMO_ROUTING == "1"``,
else PAPER).

The PAPER engine keeps running internally (planner, Risk Engine V2 open-risk / sizing state, conflict engine, durable
cycles): it is never switched off from here. ``runtime.config.RuntimeConfig`` never reads this flag, so its
fingerprint is unchanged. Pure env reads; never raises.
"""
import os

RESULTS_SOURCE_ENV = "AI_FLOOR_RESULTS_SOURCE"
OANDA_DEMO = "OANDA_DEMO"
PAPER_HIDDEN_NOTE = ("Modo solo demo OANDA (AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO): el ledger PAPER es interno y no se "
                     "muestra. Los resultados están en Resumen, Operaciones y Floor (demo OANDA).")


def _env(env):
    return os.environ if env is None else env


def oanda_only(env=None):
    """True only for the exact value ``OANDA_DEMO``."""
    try:
        return _env(env).get(RESULTS_SOURCE_ENV) == OANDA_DEMO
    except Exception:  # noqa: BLE001 - unreadable env: current behaviour
        return False


def demo_results(env=None):
    """True when the owner-facing results must be the OANDA demo ones (OANDA-only mode or routing ON)."""
    env = _env(env)
    try:
        return oanda_only(env) or env.get("AI_FLOOR_BROKER_DEMO_ROUTING") == "1"
    except Exception:  # noqa: BLE001
        return False
