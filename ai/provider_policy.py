"""Which AI provider the floor is allowed to call.

The selected mode is exclusive. A failure of the selected provider does not fall back to another paid
provider. ``deterministic`` remains the default and does not call a network.
"""
import os


MODES = ("deterministic", "openai", "xai")
# Explicit: there is no silent fallback. Rollback is choosing the previous mode and deploying that config.
FALLBACK = None

XAI_MODEL = "grok-4.7"
XAI_BASE_URL = "https://api.x.ai/v1"
OPENAI_MODEL = "gpt-5.6-terra"


def selection(mode):
    """``{"mode", "fallback"}``. ``fallback`` is always None. Unknown modes raise."""
    if mode not in MODES:
        raise ValueError("invalid provider mode")
    return {"mode": mode, "fallback": FALLBACK}


def build_provider(mode, *, environ=None):
    """The provider for ``mode``. Does not substitute a different network provider on failure."""
    environ = os.environ if environ is None else environ
    chosen = selection(mode)["mode"]
    if chosen == "deterministic":
        from ai.provider import DeterministicAIProvider
        return DeterministicAIProvider()
    if chosen == "openai":
        from ai.openai_provider import OpenAIProvider
        return OpenAIProvider(timeout=float(environ.get("OPENAI_TIMEOUT_SECONDS", "30")))
    from ai.xai_provider import XAIProvider
    return XAIProvider(timeout=float(environ.get("XAI_TIMEOUT_SECONDS", "30")))
