"""AI provider abstraction.

No network calls, no API keys. A real LLM provider can be plugged in later by
implementing the same `generate(request) -> AIResponse` interface; nothing in
the deterministic Risk Engine, Paper Broker or scouts needs to change.
"""
from abc import ABC, abstractmethod

from ai.contracts import AI_SCHEMA_VERSION, AIResponse


class AIProvider(ABC):
    """Minimal provider interface."""

    @abstractmethod
    def generate(self, request):  # pragma: no cover - interface only
        raise NotImplementedError


class DeterministicAIProvider(AIProvider):
    """Safe default provider: it only ever echoes and summarizes evidence
    that was actually supplied on the request. It never invents a price,
    level, event or bias that is not already present in
    ``request.deterministic_evidence``.
    """

    name = "deterministic-echo"
    model = "rule-based-v1"

    @property
    def metadata(self):
        return {"provider": self.name, "model": self.model}

    def generate(self, request):
        evidence = [item for item in request.deterministic_evidence if isinstance(item, dict)]
        if not evidence:
            return AIResponse(
                AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol,
                request.agent_name, "NO_DATA", model_metadata=self.metadata,
            )
        supporting = tuple(item["evidence_id"] for item in evidence if item.get("evidence_id"))
        if request.role in {"setup_reviewer", "trade_reviewer"}:
            return self._review_response(request, evidence, supporting)
        return self._specialist_response(request, evidence, supporting)

    def _specialist_response(self, request, evidence, supporting):
        biases = [item.get("bias") for item in evidence if item.get("bias") in {"BULLISH", "BEARISH"}]
        if biases and all(value == biases[0] for value in biases):
            bias = biases[0]
        elif biases:
            bias = "NEUTRAL"
        else:
            bias = "UNKNOWN"
        confidence = min(1.0, 0.4 + 0.15 * len(supporting))
        observations = tuple(
            f"evidence {item['evidence_id']} reports bias={item.get('bias', 'UNKNOWN')}"
            for item in evidence if item.get("evidence_id")
        )
        return AIResponse(
            AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
            "OK", bias=bias, confidence=confidence, observations=observations,
            supporting_evidence=supporting, model_metadata=self.metadata,
        )

    def _review_response(self, request, evidence, supporting):
        by_id = {item.get("evidence_id"): item for item in evidence}
        if request.role == "setup_reviewer":
            setup = by_id.get("setup_status", {})
            status = setup.get("status")
            side = setup.get("side")
            if status == "NO_SETUP":
                return AIResponse(
                    AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
                    "OK", bias="UNKNOWN", confidence=1.0, recommendation="INSUFFICIENT_DATA",
                    supporting_evidence=supporting, model_metadata=self.metadata,
                )
            if status == "WATCH":
                return AIResponse(
                    AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
                    "OK", bias="UNKNOWN", confidence=0.5, recommendation="CAUTION",
                    supporting_evidence=supporting, model_metadata=self.metadata,
                )
            expected = "BULLISH" if side == "LONG" else "BEARISH" if side == "SHORT" else "UNKNOWN"
            specialist_biases = [
                item.get("bias") for key, item in by_id.items()
                if key != "setup_status" and item.get("bias") not in (None, "UNKNOWN")
            ]
            agree = sum(1 for value in specialist_biases if value == expected)
            conflict = sum(1 for value in specialist_biases if value != expected)
            if not specialist_biases:
                recommendation, confidence = "INSUFFICIENT_DATA", 0.3
            elif conflict > agree:
                recommendation, confidence = "DISAGREE", 0.3
            elif conflict > 0:
                recommendation, confidence = "CAUTION", 0.6
            else:
                recommendation, confidence = "AGREE", 0.8
            return AIResponse(
                AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
                "OK", bias=expected, confidence=confidence, recommendation=recommendation,
                supporting_evidence=supporting, model_metadata=self.metadata,
            )
        plan = by_id.get("trade_plan", {})
        invalidation = plan.get("invalidation")
        risk_reward = plan.get("risk_reward")
        macro_caution = any(item.get("high_impact_active_window") for item in evidence)
        specialist_conflicts = [
            item.get("bias") for key, item in by_id.items()
            if key != "trade_plan" and item.get("bias") not in (None, "UNKNOWN")
        ]
        expected = "BULLISH" if plan.get("side") == "LONG" else "BEARISH" if plan.get("side") == "SHORT" else "UNKNOWN"
        conflicts = sum(1 for value in specialist_conflicts if value != expected)
        if not invalidation or (isinstance(risk_reward, (int, float)) and risk_reward < 3):
            recommendation, confidence = "REJECT_RECOMMENDATION", 0.4
        elif macro_caution or conflicts > 0:
            recommendation, confidence = "CAUTION", 0.6
        else:
            recommendation, confidence = "ACCEPT", 0.8
        return AIResponse(
            AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
            "OK", bias=expected, confidence=confidence, recommendation=recommendation,
            supporting_evidence=supporting, model_metadata=self.metadata,
        )


class FakeAIProvider(AIProvider):
    """Test double. Returns whatever the caller configures, including
    adversarial/hallucinated responses, so grounding and rejection paths can
    be exercised without any network access.
    """

    def __init__(self, respond_fn=None, response=None, raises=None):
        self._respond_fn = respond_fn
        self._response = response
        self._raises = raises
        self.calls = []

    @property
    def metadata(self):
        return {"provider": "fake", "model": "test-double"}

    def generate(self, request):
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        if self._respond_fn is not None:
            return self._respond_fn(request)
        return self._response
