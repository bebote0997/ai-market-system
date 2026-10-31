"""Short Spanish presentation of agent prose and evidence blobs.

No network and no model call: a fixed glossary of the phrases the floor agents actually write, cached in
process memory. Text that does not match is left unchanged (the caller shows the original). Nothing here
invents a market fact that was not in the source sentence.
"""
from functools import lru_cache
from html import escape
import re

# English function / trading words that mean the sentence still needs a Spanish summary.
_ENGLISH = re.compile(
    r"\b(the|and|with|while|across|this|that|from|have|has|been|are|is|not|for|both|when|which|"
    r"their|there|would|could|should|remains|evidence|neutral|liquidity|timeframes?|setup|caution|"
    r"upcoming|reported|direction|above|below|supplied|warranted|confluence|executable|mixed|"
    r"sweeps?|retracement|indeterminate|calendar|high-impact|specialist|deterministic)\b",
    re.IGNORECASE)

# (pattern, Spanish fact). Order is the order facts are spoken. A sentence can contribute several.
_FACTS = (
    (r"no directional confluence|no executable setup|executable setup is not",
     "No hay confluencia direccional ni un setup operable."),
    (r"caution is warranted", "Conviene ser prudente."),
    (r"remains watch|\bwatch\b", "La evaluación sigue en observación."),
    (r"non-directional|neutral in both bias|all supplied timeframes are neutral|structure_state",
     "Los marcos están neutrales, sin dirección."),
    (r"\bbos\b|break of structure", "Hay ruptura de estructura (BOS)."),
    (r"retracement", "Hay retroceso en algunos marcos y no en todos."),
    (r"liquidity below|favor liquidity below", "La liquidez de marcos altos apunta por debajo del precio."),
    (r"more concentrated above|liquidity above", "En el marco corto la liquidez está más arriba."),
    (r"no sweeps|order blocks|evidence is mixed|\bmixed\b", "Sin barridas claras: la evidencia está mixta."),
    (r"high-impact|calendar risk|two-sided", "Hay riesgo de calendario por eventos de alto impacto, en ambos sentidos."),
    (r"no active event window|indeterminate|no timestamps",
     "Sin ventana de evento activa: la dirección queda indeterminada."),
    (r"\bbullish\b", "Sesgo de compra."),
    (r"\bbearish\b", "Sesgo de venta."),
    (r"valid setup", "Setup válido."),
    (r"\binvalid\b|no setup", "Sin setup operable."),
)
_SHORT = 280


def _clip(text, limit=_SHORT):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,.;:") + "…"


@lru_cache(maxsize=1024)
def _summarize(text):
    """Cached on the normalised sentence. ``None`` when the English did not match the glossary."""
    if not text:
        return None
    if not _ENGLISH.search(text):
        return _clip(text)
    low = text.lower()
    facts = []
    for pattern, sentence in _FACTS:
        if re.search(pattern, low) and sentence not in facts:
            facts.append(sentence)
    if not facts:
        return None
    return _clip(" ".join(facts))


def summarize_es(text):
    """Spanish summary, the original when it is already Spanish, or ``None`` if it cannot be summarised."""
    return _summarize(" ".join(str(text or "").split()))


def history_fields(text, at=None):
    """One history row: ``text`` is the original; ``summary`` only when a shorter Spanish line exists."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    item = {"text": raw[:4000]}
    if at:
        item["at"] = at
    summary = summarize_es(raw)
    if summary and summary != raw:
        item["summary"] = summary
    return item


def _dict_brief(value):
    from ui import i18n_es as es
    parts = []
    for key, label in (("symbol", "Símbolo"), ("status", "Estado"), ("final_status", "Estado final"),
                       ("setup_status", "Setup"), ("side", "Lado"), ("bias", "Tendencia"),
                       ("decision", "Decisión"), ("reason", "Razón")):
        raw = value.get(key)
        if raw in (None, ""):
            continue
        shown = str(raw) if key == "symbol" else es.status_text(raw)
        parts.append(f"{label}: {shown}")
    prose = value.get("reasoning_summary") or value.get("summary") or value.get("analysis")
    if isinstance(prose, str):
        line = summarize_es(prose)
        if line:
            parts.append(line)
    if not parts:
        return f"{len(value)} campos registrados."
    return _clip(" · ".join(parts))


def brief_spanish(value):
    """One short Spanish line for a string, dict or list. ``None`` when a string cannot be summarised."""
    if isinstance(value, str):
        return summarize_es(value)
    if isinstance(value, dict):
        return _dict_brief(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "Sin registros."
        head = f"{len(value)} registro" + ("s" if len(value) != 1 else "") + "."
        first = value[0]
        extra = brief_spanish(first) if isinstance(first, (dict, str)) else None
        return _clip(head + ((" " + extra) if extra else ""))
    return None


def _more(raw_html):
    return ('<details class="more"><summary><span class="when-closed">Ver más</span>'
            '<span class="when-open">Ocultar</span></summary>'
            f'<div class="raw">{raw_html}</div></details>')


def present_summary(value):
    """Bubble for a short value; a Spanish line plus «Ver más» when the raw text is long or structured.

    A string the glossary cannot summarise is shown in the original language (clipped, with «Ver más»
    for the rest). HTML is escaped here.
    """
    from ui.json_views import details
    if value is None:
        return ""
    if isinstance(value, str) and len(" ".join(value.split())) <= 180 and not _ENGLISH.search(value):
        return f'<div class="bubble">{escape(value, quote=True)}</div>'
    raw_html = details(value) if not isinstance(value, str) else escape(value, quote=True)
    brief = brief_spanish(value)
    if isinstance(value, str) and brief is None:
        clipped = _clip(value, 240)
        lead = f'<div class="bubble">{escape(clipped, quote=True)}</div>'
        return lead if clipped == " ".join(value.split()) else lead + _more(raw_html)
    if not brief:
        return f'<div class="bubble">{raw_html}</div>' if len(raw_html) <= 220 else _more(raw_html)
    return f'<div class="bubble">{escape(brief, quote=True)}</div>' + _more(raw_html)
