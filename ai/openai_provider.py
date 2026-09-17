"""OpenAI Responses adapter. Advisory output only; ai.runtime validates grounding."""
from datetime import datetime
import json
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai.contracts import AIResponse
from ai.provider import AIProvider
from ai import prompts
from runtime.retry_after import retry_after_seconds


class OpenAIProviderError(RuntimeError):
    """A sanitized provider failure; never carries a response body or credential."""

    def __init__(self, kind, *, http_status=None, error_type=None, error_code=None, retry_after=None):
        self.kind = kind
        self.http_status = http_status
        self.error_type = error_type
        self.error_code = error_code
        self.retry_after = retry_after
        super().__init__(kind)


_PROMPTS = {
    "structure_specialist": prompts.STRUCTURE_PROMPT,
    "liquidity_specialist": prompts.LIQUIDITY_PROMPT,
    "macro_specialist": prompts.MACRO_PROMPT,
    "setup_reviewer": prompts.SETUP_REVIEW_PROMPT,
    "trade_reviewer": prompts.TRADE_REVIEW_PROMPT,
}


def _field(kind):
    return {"type": kind}


_PROPERTIES = {
    "schema_version": _field("string"),
    "run_id": _field("string"),
    "as_of": _field("string"),
    "symbol": _field("string"),
    "agent_name": _field("string"),
    "status": {"type": "string", "enum": ["OK", "PARTIAL", "NO_DATA", "ERROR"]},
    "bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"]},
    "confidence": _field("number"),
    "recommendation": {"type": ["string", "null"]},
    "observations": {"type": "array", "items": _field("string")},
    "supporting_evidence": {"type": "array", "items": _field("string")},
    "conflicting_evidence": {"type": "array", "items": _field("string")},
    "risks": {"type": "array", "items": _field("string")},
    "invalidation_conditions": {"type": "array", "items": _field("string")},
    "warnings": {"type": "array", "items": _field("string")},
    "reasoning_summary": {"type": ["string", "null"]},
}
RESPONSE_SCHEMA = {
    "type": "object", "properties": _PROPERTIES,
    "required": list(_PROPERTIES), "additionalProperties": False,
}


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported request value")


def _http_json(payload, api_key, timeout):
    body = json.dumps(payload, default=_default, allow_nan=False).encode("utf-8")
    request = Request(
        "https://api.openai.com/v1/responses", data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, *, api_key=None, model=None, timeout=30, retries=2,
                 transport=None, sleep=time.sleep):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
        self.timeout = float(timeout)
        self.retries = int(retries)
        if self.timeout <= 0 or not 0 <= self.retries <= 5:
            raise ValueError("invalid OpenAI timeout or retries")
        self._transport = transport or _http_json
        self._sleep = sleep
        self.last_failure = None
        self.last_usage = {}

    @property
    def metadata(self):
        return {"provider": self.name, "model": self.model}

    @property
    def health(self):
        if not self.api_key:
            return "NOT_CONFIGURED"
        return "ERROR" if self.last_failure else "READY" if self.last_usage else "DEGRADED"

    def _post(self, payload):
        if not self.api_key:
            raise OpenAIProviderError("NOT_CONFIGURED")
        for attempt in range(self.retries + 1):
            http_status = error_type = error_code = retry_after = None
            try:
                return self._transport(payload, self.api_key, self.timeout)
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
            except (URLError, TimeoutError, ConnectionError):
                transient, kind = True, "CONNECTION_ERROR"
            if not transient or attempt == self.retries or (retry_after is not None and retry_after > 15):
                self.last_failure = kind
                raise OpenAIProviderError(kind, http_status=http_status, error_type=error_type,
                                          error_code=error_code, retry_after=retry_after) from None
            delay = retry_after if retry_after is not None else min(4.0, 0.4 * 2 ** attempt)
            self._sleep(delay + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    def generate(self, request):
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
        properties = {name: dict(spec) for name, spec in _PROPERTIES.items()}
        for name, value in (("schema_version", request.schema_version),
                            ("run_id", request.run_id), ("as_of", request.as_of.isoformat()),
                            ("symbol", request.symbol), ("agent_name", request.agent_name)):
            properties[name] = {"type": "string", "enum": [value]}
        response_schema = {**RESPONSE_SCHEMA, "properties": properties}
        payload = {
            "model": self.model, "store": False,
            "instructions": role_prompt + "\nInterpret only supplied evidence. NO_DATA is valid. Never authorize execution or change risk. Return a concise reasoning_summary, not private reasoning.",
            "input": json.dumps(prompt, default=_default, allow_nan=False),
            "text": {"format": {"type": "json_schema", "name": "ai_response",
                                "strict": True, "schema": response_schema}},
        }
        raw = self._post(payload)
        if not isinstance(raw, dict) or raw.get("status") != "completed":
            self.last_failure = "INCOMPLETE_RESPONSE"
            raise OpenAIProviderError("INCOMPLETE_RESPONSE")
        try:
            parts = [part["text"] for item in raw["output"] if item.get("type") == "message"
                     for part in item["content"] if part.get("type") == "output_text"]
            if len(parts) != 1:
                raise ValueError("expected exactly one output text")
            data = json.loads(parts[0])
            if not isinstance(data, dict) or set(data) != set(_PROPERTIES):
                raise ValueError("invalid response shape")
            usage = raw.get("usage") or {}
            safe_usage = {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
                          if isinstance(usage.get(k), int) and usage[k] >= 0}
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
        except (KeyError, TypeError, ValueError, IndexError):
            self.last_failure = "INVALID_STRUCTURED_RESPONSE"
            raise OpenAIProviderError("INVALID_STRUCTURED_RESPONSE") from None
        self.last_failure = None
        self.last_usage = safe_usage
        return response
