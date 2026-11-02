"""Identifiers and outcomes for PAPER audit; never used as execution gates."""


def setup_id(assessment):
    """Stable setup identity; the algorithm lives with the Setup Validator (V2 Phase 3, unchanged values)."""
    from agents.setup_validator import setup_identity
    return setup_identity(assessment)[0]


def setup_log_fields(assessment):
    """(setup_status, side, setup_warnings) as key=value-safe tokens for log lines; '-' when absent."""
    if assessment is None:
        return "-", "-", "-"
    warnings = ",".join("_".join(str(w).split()) for w in (assessment.warnings or ()) if str(w).strip())
    return assessment.status or "-", assessment.side or "-", warnings or "-"
