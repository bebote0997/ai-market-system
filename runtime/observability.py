"""Identifiers and outcomes for PAPER audit; never used as execution gates."""
import json
import uuid


def setup_id(assessment):
    if assessment is None or assessment.status != "VALID_SETUP":
        return None
    structure = assessment.evidence[0] if assessment.evidence else {}
    signal = structure.get("15m", {})
    bos = signal.get("bos") or {}
    retracement = signal.get("retracement") or {}
    anchor = (
        {"kind": "BOS", "timestamp": str(bos.get("break_timestamp")),
         "level": bos.get("broken_level"), "direction": bos.get("direction")}
        if bos else
        {"kind": "RETRACEMENT", "impulse": str(retracement.get("impulse_timestamp")),
         "protected": str(retracement.get("protected_timestamp")),
         "impulse_price": retracement.get("impulse_low", retracement.get("impulse_high")),
         "protected_price": retracement.get("protected_high", retracement.get("protected_low"))}
    )
    identity = {"symbol": assessment.symbol, "side": assessment.side,
                "invalidation": assessment.invalidation, "anchor": anchor}
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True)))
