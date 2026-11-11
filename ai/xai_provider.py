"""xAI Responses adapter. Advisory output only; ai.runtime validates grounding.

Official API (docs.x.ai, checked 2026-11-07):

* ``POST {XAI_BASE_URL}/responses`` with ``XAI_BASE_URL`` default ``https://api.x.ai/v1``
* ``Authorization: Bearer`` from ``XAI_API_KEY`` only. ``OPENAI_API_KEY`` is never read.
* Model ``XAI_MODEL``, default ``grok-4.7``.
* ``store: false``. No tools (no web search, no X search, no function calls).
* Structured output uses the same ``text.format.json_schema`` shape as the OpenAI Responses adapter.
* Reasoning effort is ``low`` so a trading advisory call stays inside the output-token cap.

A selected xAI provider that fails stays failed. This module does not call OpenAI.

``XAI_RATE_LIMIT_V2=1`` serializes calls and retries HTTP 429 with backoff that
honors Retry-After, then fails closed. Any other value, including unset, keeps
the legacy retry loop below unchanged.
"""
from datetime import datetime, timezone
import json
import logging
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai.contracts import AIResponse, VALID_RECOMMENDATIONS, evidence_ids
from ai.openai_provider import RESPONSE_SCHEMA, _PROPERTIES
from ai.provider import AIProvider
from ai.provider_policy import XAI_BASE_URL, XAI_MODEL
from ai import prompts
from ai import xai_rate_limit
from runtime import health_hooks
from runtime.retry_after import retry_after_seconds


LOG = logging.getLogger("ai_floor.xai")


class XAIProviderError(RuntimeError):
    """A sanitized provider failure. The message is the kind only."""

    def __init__(self, kind, *, http_status=None, error_type=None, error_code=None, retry_after=None,
                 timed_out=False):
        self.kind = kind
        self.http_status = http_status
        self.error_type = error_type
        self.error_code = error_code
        self.retry_after = retry_after
        self.timed_out = timed_out
        super().__init__(kind)


_PROMPTS = {
    "structure_specialist": prompts.STRUCTURE_PROMPT,
    "liquidity_specialist": prompts.LIQUIDITY_PROMPT,
    "macro_specialist": prompts.MACRO_PROMPT,
    "setup_reviewer": prompts.SETUP_REVIEW_PROMPT,
    "trade_reviewer": prompts.TRADE_REVIEW_PROMPT,
}
_TOOL_TYPES = {"function_call", "tool_call", "web_search_call", "x_search_call", "mcp_call", "custom_tool_call"}
_DEFAULT_MAX_INPUT_TOKENS = 6000
_DEFAULT_MAX_OUTPUT_TOKENS = 800
_DEFAULT_MAX_TOTAL_TOKENS = 8000


def responses_url(base):
    """HTTPS Responses endpoint. Rejects anything that is not an https URL without spaces."""
    text = (XAI_BASE_URL if base is None else str(base)).strip().rstrip("/")
    if not text.startswith("https://") or " " in text or "\n" in text:
        raise ValueError("XAI_BASE_URL must be an https URL")
    return text + "/responses"


def _http_json(url, payload, api_key, timeout):
    body = json.dumps(payload, default=_default, allow_nan=False).encode("utf-8")
    request = Request(
        url, data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported request value")


def _agent_token(name):
    """A log-safe agent id. Anything else becomes ``-`` so the line cannot grow new fields."""
    if isinstance(name, str) and 1 <= len(name) <= 64 and name.replace("_", "").isalnum():
        return name
    return "-"


def _positive_int(raw, default, name, *, upper):
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    if not text.isdigit() or not 1 <= int(text) <= upper:
        raise ValueError(f"invalid {name}")
    return int(text)


class XAIProvider(AIProvider):
    name = "xai"

    def __init__(self, *, api_key=None, model=None, base_url=None, timeout=30, retries=None,
                 max_input_tokens=None, max_output_tokens=None, max_total_tokens=None,
                 transport=None, sleep=time.sleep, monotonic=time.monotonic):
        self.api_key = api_key if api_key is not None else os.environ.get("XAI_API_KEY")
        self.model = model or os.environ.get("XAI_MODEL", XAI_MODEL)
        self.base_url = responses_url(base_url if base_url is not None else os.environ.get("XAI_BASE_URL"))
        self.timeout = float(timeout if timeout is not None else os.environ.get("XAI_TIMEOUT_SECONDS", "30"))
        retry_raw = retries if retries is not None else os.environ.get("XAI_RETRIES", "2")
        self.retries = int(retry_raw)
        self.max_input_tokens = _positive_int(
            max_input_tokens if max_input_tokens is not None else os.environ.get("XAI_MAX_INPUT_TOKENS"),
            _DEFAULT_MAX_INPUT_TOKENS, "XAI_MAX_INPUT_TOKENS", upper=200_000)
        self.max_output_tokens = _positive_int(
            max_output_tokens if max_output_tokens is not None else os.environ.get("XAI_MAX_OUTPUT_TOKENS"),
            _DEFAULT_MAX_OUTPUT_TOKENS, "XAI_MAX_OUTPUT_TOKENS", upper=32_000)
        self.max_total_tokens = _positive_int(
            max_total_tokens if max_total_tokens is not None else os.environ.get("XAI_MAX_TOTAL_TOKENS"),
            _DEFAULT_MAX_TOTAL_TOKENS, "XAI_MAX_TOTAL_TOKENS", upper=500_000)
        if self.timeout <= 0 or not 0 <= self.retries <= 5:
            raise ValueError("invalid xAI timeout or retries")
        self._transport = transport or _http_json
        self._sleep = sleep
        self.last_failure = None
        self.last_usage = {}
        self._monotonic = monotonic
        self.deadline = None
        self.last_call = None

    @property
    def metadata(self):
        return {"provider": self.name, "model": self.model}

    @property
    def health(self):
        if not self.api_key:
            return "NOT_CONFIGURED"
        return "ERROR" if self.last_failure else "READY" if self.last_usage else "DEGRADED"

    def _attempt(self, number, started, http_status=None, kind=None):
        if self.last_call is not None:
            self.last_call["attempts"].append({
                "attempt": number, "http_status": http_status, "kind": kind,
                "latency_ms": round((self._monotonic() - started) * 1000, 3)})

    def _fail(self, outcome):
        self.last_failure = outcome["kind"]
        raise XAIProviderError(
            outcome["kind"], http_status=outcome["http_status"], error_type=outcome["error_type"],
            error_code=outcome["error_code"], retry_after=outcome["retry_after"],
            timed_out=outcome["timed_out"]) from None

    def _transport_once(self, payload):
        """One HTTP try. ``ok`` carries the body. A failure dict matches the legacy classifier."""
        http_status = error_type = error_code = retry_after = None
        timed_out = False
        started = self._monotonic()
        timeout = self.timeout
        if self.deadline is not None:
            remaining = self.deadline - started
            if remaining <= 0:
                raise XAIProviderError("AI_TIME_BUDGET_EXHAUSTED")
            timeout = min(self.timeout, remaining)
        try:
            raw = self._transport(self.base_url, payload, self.api_key, timeout)
        except HTTPError as exc:
            http_status = exc.code
            retry_after = retry_after_seconds(exc.headers)
            transient = exc.code in {408, 409, 429} or 500 <= exc.code <= 599
            try:
                body = json.load(exc)
                detail = body.get("error", {}) if isinstance(body, dict) else {}
                if isinstance(detail, dict):
                    error_code = detail.get("code")
                    error_type = detail.get("type")
            except (ValueError, TypeError, AttributeError):
                pass
            if not isinstance(error_code, str) or not error_code.replace("_", "").isalnum() or len(error_code) > 64:
                error_code = None
            if not isinstance(error_type, str) or not error_type.replace("_", "").isalnum() or len(error_type) > 64:
                error_type = None
            if (error_type == "insufficient_quota" or error_code in {
                    "insufficient_quota", "billing_hard_limit_reached", "credit_balance_exhausted"}):
                kind, transient = "QUOTA_EXHAUSTED", False
            else:
                kind = ("RATE_LIMITED" if exc.code == 429 else
                        "AUTH_ERROR" if exc.code in {401, 403} else
                        "MODEL_UNAVAILABLE" if exc.code == 404 else "PROVIDER_ERROR")
        except (URLError, TimeoutError, ConnectionError) as exc:
            transient, kind = True, "CONNECTION_ERROR"
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        else:
            return {"ok": True, "raw": raw, "started": started}
        return {
            "ok": False, "started": started, "http_status": http_status, "kind": kind,
            "transient": transient, "error_type": error_type, "error_code": error_code,
            "retry_after": retry_after, "timed_out": timed_out,
        }

    def _legacy_delay(self, attempt, outcome):
        """The pre-flag wait. Retry-After above 15 seconds does not wait; the call stops."""
        retry_after = outcome["retry_after"]
        delay = retry_after if retry_after is not None else min(4.0, 0.4 * 2 ** attempt)
        budget_cut = self.deadline is not None and self._monotonic() + delay >= self.deadline
        stop = (not outcome["transient"] or attempt == self.retries
                or (retry_after is not None and retry_after > 15) or budget_cut)
        return delay, stop

    def _post(self, payload, agent_name=None):
        if not self.api_key:
            raise XAIProviderError("NOT_CONFIGURED")
        if xai_rate_limit.enabled():
            with xai_rate_limit.CALL_LOCK:
                return self._post_v2(payload, agent_name)
        return self._post_legacy(payload)

    def _post_legacy(self, payload):
        for attempt in range(self.retries + 1):
            outcome = self._transport_once(payload)
            if outcome["ok"]:
                self._attempt(attempt + 1, outcome["started"], http_status=200)
                return outcome["raw"]
            self._attempt(attempt + 1, outcome["started"], http_status=outcome["http_status"],
                          kind=outcome["kind"])
            delay, stop = self._legacy_delay(attempt, outcome)
            if stop:
                self._fail(outcome)
            self._sleep(delay + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    def _budget_stop(self, last):
        """The wall budget cannot hold another full timeout. Fail closed, no further HTTP."""
        if last is None or last["kind"] == "RATE_LIMITED":
            self.last_failure = "RATE_LIMITED"
            http_status = None if last is None else last["http_status"]
            raise XAIProviderError(
                "RATE_LIMITED", http_status=http_status,
                error_type=None if last is None else last["error_type"],
                error_code=None if last is None else last["error_code"],
                retry_after=None if last is None else last["retry_after"],
                timed_out=False if last is None else last["timed_out"]) from None
        self._fail(last)

    def _sleep_reserved(self, wait):
        """Charge the wait up front. If the sleep raises, keep only the time that passed."""
        started = self._monotonic()
        xai_rate_limit.reserve_wait(wait)
        try:
            self._sleep(wait)
        except BaseException:
            xai_rate_limit.release_wait(wait)
            elapsed = self._monotonic() - started
            if elapsed > 0:
                xai_rate_limit.charge(min(elapsed, wait), self._monotonic())
            raise
        xai_rate_limit.finish_wait(self._monotonic())

    def _post_v2(self, payload, agent_name):
        """Wall budgets cover HTTP and waits. 429 uses backoff; other failures keep the legacy schedule."""
        attempt = 0
        last = None
        call_started = self._monotonic()
        while True:
            if not xai_rate_limit.attempt_fits(self._monotonic(), call_started, self.timeout):
                self._budget_stop(last)
            outcome = self._transport_once(payload)
            xai_rate_limit.charge(self._monotonic() - outcome["started"], self._monotonic())
            if outcome["ok"]:
                self._attempt(attempt + 1, outcome["started"], http_status=200)
                return outcome["raw"]
            last = outcome
            self._attempt(attempt + 1, outcome["started"], http_status=outcome["http_status"],
                          kind=outcome["kind"])
            if outcome["kind"] == "RATE_LIMITED" and outcome["transient"]:
                wait = xai_rate_limit.plan_wait(
                    attempt, outcome["retry_after"], now=self._monotonic(), call_started=call_started,
                    timeout=self.timeout, uniform=random.uniform)
                if wait is not None and self.deadline is not None and self._monotonic() + wait >= self.deadline:
                    wait = None
                if wait is None:
                    self._fail(outcome)
                LOG.info("component=xai event=RATE_LIMIT_RETRY agent=%s attempt=%s wait_seconds=%s",
                         _agent_token(agent_name), attempt + 1, f"{wait:.3f}")
                self._sleep_reserved(wait)
                attempt += 1
                continue
            delay, stop = self._legacy_delay(attempt, outcome)
            if stop or attempt > self.retries:
                self._fail(outcome)
            wait = delay + random.uniform(0, 0.1)
            if not xai_rate_limit.wait_fits(self._monotonic(), call_started, wait, self.timeout):
                self._fail(outcome)
            self._sleep_reserved(wait)
            attempt += 1

    def generate(self, request):
        started = time.perf_counter()
        self.last_call = {"requested_at": datetime.now(timezone.utc).isoformat(), "attempts": [], "response_id": None}
        try:
            response = self._generate_unobserved(request)
        except Exception as error:
            health_hooks.emit("provider_call_observed", provider=self, request=request, error=error,
                              latency_ms=(time.perf_counter() - started) * 1000)
            raise
        health_hooks.emit("provider_call_observed", provider=self, request=request, response=response,
                          latency_ms=(time.perf_counter() - started) * 1000)
        return response

    def _generate_unobserved(self, request):
        role_prompt = _PROMPTS.get(request.role)
        if role_prompt is None:
            raise ValueError("unsupported AI role")
        prompt = {
            "schema_version": request.schema_version, "run_id": request.run_id,
            "as_of": request.as_of, "symbol": request.symbol, "agent_name": request.agent_name,
            "role": request.role, "market_context": request.market_context,
            "deterministic_evidence": request.deterministic_evidence,
            "allowed_actions": request.allowed_actions, "constraints": request.constraints,
            "prompt_version": request.prompt_version,
        }
        encoded = json.dumps(prompt, default=_default, allow_nan=False).encode("utf-8")
        if len(encoded) // 4 > self.max_input_tokens:
            self.last_failure = "TOKEN_BUDGET_EXCEEDED"
            raise XAIProviderError("TOKEN_BUDGET_EXCEEDED")
        properties = {name: dict(spec) for name, spec in _PROPERTIES.items()}
        for name, value in (("schema_version", request.schema_version),
                            ("run_id", request.run_id), ("as_of", request.as_of.isoformat()),
                            ("symbol", request.symbol), ("agent_name", request.agent_name)):
            properties[name] = {"type": "string", "enum": [value]}
        properties["recommendation"] = {"type": ["string", "null"],
                                         "enum": [None, *sorted(VALID_RECOMMENDATIONS)]}
        allowed_evidence = sorted(evidence_ids(request.deterministic_evidence))
        if allowed_evidence:
            for name in ("supporting_evidence", "conflicting_evidence"):
                properties[name] = {"type": "array", "items": {"type": "string", "enum": allowed_evidence}}
        response_schema = {**RESPONSE_SCHEMA, "properties": properties}
        payload = {
            "model": self.model, "store": False,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": {"effort": "low"},
            "instructions": role_prompt + "\nInterpret only supplied evidence. NO_DATA is valid. Never authorize execution or change risk. Return a concise reasoning_summary, not private reasoning.",
            "input": encoded.decode("utf-8"),
            "text": {"format": {"type": "json_schema", "name": "ai_response",
                                "strict": True, "schema": response_schema}},
        }
        if "tools" in payload:
            raise AssertionError("xAI trading calls do not enable tools")
        raw = self._post(payload, request.agent_name)
        return self._parse(raw, request)

    def _parse(self, raw, request):
        response_id = raw.get("id") if isinstance(raw, dict) else None
        if (isinstance(response_id, str) and 0 < len(response_id) <= 128
                and response_id.replace("_", "").replace("-", "").isalnum()):
            self.last_call["response_id"] = response_id
        if not isinstance(raw, dict) or raw.get("status") != "completed":
            self.last_failure = "INCOMPLETE_RESPONSE"
            raise XAIProviderError("INCOMPLETE_RESPONSE")
        output = raw.get("output")
        if not isinstance(output, list) or any(
                isinstance(item, dict) and item.get("type") in _TOOL_TYPES for item in output):
            self.last_failure = "INVALID_STRUCTURED_RESPONSE"
            raise XAIProviderError("INVALID_STRUCTURED_RESPONSE")
        try:
            parts = [part["text"] for item in output if isinstance(item, dict) and item.get("type") == "message"
                     for part in item.get("content") or () if isinstance(part, dict) and part.get("type") == "output_text"]
            if len(parts) != 1:
                raise ValueError("expected exactly one output text")
            data = json.loads(parts[0])
            if not isinstance(data, dict) or set(data) != set(_PROPERTIES):
                raise ValueError("invalid response shape")
            usage = raw.get("usage") or {}
            safe_usage = {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
                          if isinstance(usage.get(k), int) and usage[k] >= 0}
            if safe_usage.get("total_tokens", 0) > self.max_total_tokens:
                self.last_failure = "TOKEN_BUDGET_EXCEEDED"
                raise XAIProviderError("TOKEN_BUDGET_EXCEEDED")
            metadata = {**self.metadata, "prompt_version": request.prompt_version,
                        "schema_version": request.schema_version, **safe_usage}
            response = AIResponse(
                data["schema_version"], data["run_id"], datetime.fromisoformat(data["as_of"]),
                data["symbol"], data["agent_name"], data["status"], bias=data["bias"],
                confidence=data["confidence"], recommendation=data["recommendation"],
                observations=tuple(data["observations"]),
                supporting_evidence=tuple(data["supporting_evidence"]),
                conflicting_evidence=tuple(data["conflicting_evidence"]),
                risks=tuple(data["risks"]), invalidation_conditions=tuple(data["invalidation_conditions"]),
                warnings=tuple(data["warnings"]), model_metadata=metadata,
                reasoning_summary=data["reasoning_summary"],
            )
        except XAIProviderError:
            raise
        except (KeyError, TypeError, ValueError, IndexError):
            self.last_failure = "INVALID_STRUCTURED_RESPONSE"
            raise XAIProviderError("INVALID_STRUCTURED_RESPONSE") from None
        self.last_failure = None
        self.last_usage = safe_usage
        return response
