"""Identifiers and outcomes for PAPER audit; never used as execution gates."""


def setup_id(assessment):
    """Stable setup identity; the algorithm lives with the Setup Validator (V2 Phase 3, unchanged values)."""
    from agents.setup_validator import setup_identity
    return setup_identity(assessment)[0]
