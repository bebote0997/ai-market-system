"""Display names of the 8 Floor characters (DEC-AGENT-NAMES-1). Single source of truth; pure, no I/O.

DISPLAY ONLY: the internal ids (payload ``agents`` keys, DB / journal names, env, enums) never change; the Floor page
(``ui/static/floor/floor.html``) receives this mapping as ``payload.names`` and the manual close block
(``ui/views/floor_actions.py``) derives its title from the Exec agent's name.
"""

# id -> (display name, long role for the drawer subtitle, short role for plates / lists)
AGENT_NAMES = {
    "Structure": ("Mateo", "Analista de estructura", "Estructura"),
    "Liquidity": ("Sofía", "Analista de liquidez", "Liquidez"),
    "Macro": ("Andrés", "Analista macro", "Macro"),
    "Risk": ("Valentina", "Revisor de la operación (riesgo)", "Riesgo"),
    "Reviewer": ("Lucía", "Revisor de consenso", "Revisor"),
    "Conflict": ("Diego", "Motor de conflictos", "Conflictos"),
    "RiskEng": ("Camila", "Motor de riesgo (límites)", "Motor de riesgo"),
    "Exec": ("Carlos", "Ejecución", "Mesa EURUSD"),
}


def display_name(agent_id):
    """Display name of ``agent_id``; an unknown id is shown as itself."""
    return AGENT_NAMES.get(agent_id, (agent_id,))[0]


def long_role(agent_id):
    entry = AGENT_NAMES.get(agent_id)
    return entry[1] if entry else ""


def short_role(agent_id):
    entry = AGENT_NAMES.get(agent_id)
    return entry[2] if entry else ""


def label(agent_id):
    """«Nombre · Rol corto» (e.g. «Carlos · Mesa EURUSD»)."""
    role = short_role(agent_id)
    return f"{display_name(agent_id)} · {role}" if role else display_name(agent_id)


def names_payload():
    """JSON-ready ``{id: {"name", "role", "short"}}`` for ``payload.names`` (a fresh copy each call)."""
    return {agent_id: {"name": name, "role": role, "short": short}
            for agent_id, (name, role, short) in AGENT_NAMES.items()}
