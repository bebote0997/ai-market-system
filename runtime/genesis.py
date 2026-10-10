"""V2 Phase 8 / P2b Sealed Genesis: the SEALED_RUNTIME side (design P8.6 5.1.3.2; R-GEN-1a/b/d; DEC-8.22-i;
Owner decisions D-1..D-4, 2026-10-09).

Behind ``RuntimeConfig.v2_sealed_genesis`` (``AI_FLOOR_V2_SEALED_GENESIS``, OFF by default; ON requires REX ON).
Implementation is not activation: the transition to SEALED_RUNTIME as the only mode happens at the authorized P4b
baseline cut, not here. With the flag OFF nothing in this module runs.

Start order in SEALED_RUNTIME (design "Startup order"):
1. ``verify_startup_context`` (steps 1-4) BEFORE ANY SQLite open: no start-mode variable may request preparation; the
   external configuration (``AI_FLOOR_GENESIS_ANCHOR`` = the OAR-G document, its ``.sig`` next to it, and
   ``AI_FLOOR_GENESIS_ALLOWED_SIGNERS``) is present; the OAR-G signature verifies (``ssh-keygen -Y verify``, namespace
   ``v2-genesis-anchor``, a principal of ``allowed_signers``); the document is canonical (``cj``); ``x_p2`` equals the
   pinned baseline (``SEALED_BASELINE_SHA``, set at the P4b cut; until then no sealed start passes outside tests) and
   ``y_p2`` equals ``AI_FLOOR_GIT_COMMIT``; the configured DB path is not a symlink, exists, resolves to
   ``db_realpath`` and has the anchored ``st_dev`` / ``st_ino`` with ``st_nlink == 1``; ``edg_genesis`` equals the
   independently recomputed expected genesis (I-G12). Returns a ``SealedContext``; nothing is written.
2. The runtime constructor requires that context (it cannot be built without steps 1-4), re-checks the file identity
   right after opening it, runs the non-economic ``recover()``, then ``seal_check`` (steps 2-5): the account is
   present and EDG equals ``edg_genesis`` before ``E0`` or the REX chain head after it. One non-economic
   ``GENESIS_SEAL_CHECK`` row records the result; on FAIL the constructor raises after writing it. There is no
   account-creation path in this mode (I-G17, I-G20).

Limits (stated): SQLite evidence cannot exclude an external writer nor detect a deletion followed by an identical
restoration (NG48); a holder of the Owner's signing key is outside the trust root. Signatures of the Owner are never
produced here.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from storage.economic_digest import H, cj, edg, expected_edg_genesis

GENESIS_ANCHOR_ENV = "AI_FLOOR_GENESIS_ANCHOR"
ALLOWED_SIGNERS_ENV = "AI_FLOOR_GENESIS_ALLOWED_SIGNERS"
START_MODE_ENV = "AI_FLOOR_START_MODE"
GIT_COMMIT_ENV = "AI_FLOOR_GIT_COMMIT"
SEALED_MODE = "SEALED_RUNTIME"
OAR_G_KIND = "V2_OAR_G/1"
AUTHORIZATION_KIND = "V2_GENESIS_AUTHORIZATION/1"
ANCHOR_NAMESPACE = "v2-genesis-anchor"
AUTHORIZATION_NAMESPACE = "v2-genesis-authorization"
GENESIS_EQUITY = 10000  # contractual starting equity (DEC-8.20); exact
GENESIS_SOURCE = "genesis"
SEAL_EVENT = "GENESIS_SEAL_CHECK"
PREPARED_EVENT = "GENESIS_PREPARED"
# X_P2: the code baseline of the sealed period, pinned at the authorized P4b baseline cut. None until then.
SEALED_BASELINE_SHA = None
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OAR_G_FIELDS = {"kind": str, "period_id_pending": str, "account_id": str, "starting_equity": int,
                "db_realpath": str, "st_dev": int, "st_ino": int, "edg_genesis": str, "psh_genesis": str,
                "max_journal_id": int, "sqlite_sequence": dict, "schema_version": int, "x_p2": str, "y_p2": str,
                "copy_sha256": str, "created_at_utc": str}
AUTHORIZATION_FIELDS = {"kind": str, "period_id": str, "db_realpath": str, "x_p2": str, "y_p2": str,
                        "starting_equity": int, "account_id": str, "not_after_utc": str}
SUBPROCESS_TIMEOUT_SECONDS = 30


class GenesisSealError(RuntimeError):
    """A sealed start (or a genesis document) failed a check; nothing economic was written."""


def _ssh_keygen():
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise GenesisSealError("ssh-keygen with -Y support is required to verify Owner signatures")
    return exe


def verify_signed_document(document_path, *, namespace, allowed_signers):
    """Verify ``<document>.sig`` over the document bytes with ``ssh-keygen -Y`` for ``namespace`` against
    ``allowed_signers``. Returns (principal, document bytes). Raises GenesisSealError on any failure."""
    document, signature, signers = Path(document_path), Path(str(document_path) + ".sig"), Path(allowed_signers)
    for path, label in ((document, "document"), (signature, "signature"), (signers, "allowed_signers")):
        if not path.is_file():
            raise GenesisSealError(f"{label} not found: {path}")
    data = document.read_bytes()
    exe = _ssh_keygen()
    try:
        found = subprocess.run([exe, "-Y", "find-principals", "-s", str(signature), "-f", str(signers)],
                               capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GenesisSealError(f"signature check could not run: {type(exc).__name__}") from None
    principals = [line.strip() for line in found.stdout.splitlines() if line.strip()]
    if found.returncode != 0 or len(principals) != 1:
        raise GenesisSealError("signer is not exactly one principal of allowed_signers")
    try:
        verified = subprocess.run([exe, "-Y", "verify", "-f", str(signers), "-I", principals[0], "-n", namespace,
                                   "-s", str(signature)], input=data, capture_output=True,
                                  timeout=SUBPROCESS_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GenesisSealError(f"signature check could not run: {type(exc).__name__}") from None
    if verified.returncode != 0:
        raise GenesisSealError(f"signature does not verify for namespace {namespace}")
    return principals[0], data


def parse_canonical(data, kind, fields):
    """A signed document: UTF-8, a JSON object whose bytes are exactly ``cj`` of itself, with ``fields`` typed."""
    try:
        text = data.decode("utf-8")
        document = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        raise GenesisSealError("document is not UTF-8 JSON") from None
    if not isinstance(document, dict) or cj(document) != text:
        raise GenesisSealError("document is not canonical JSON (cj)")
    for name, expected in fields.items():
        value = document.get(name)
        if isinstance(value, bool) or not isinstance(value, expected):
            raise GenesisSealError(f"document field {name} missing or not {expected.__name__}")
    if set(document) != set(fields):
        raise GenesisSealError("document has unexpected fields")
    if document["kind"] != kind:
        raise GenesisSealError(f"document kind is not {kind}")
    return document


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(b)


@dataclass(frozen=True)
class SealedContext:
    """Produced only by ``verify_startup_context`` (steps 1-4 passed)."""
    oar_g_json: str
    oar_g_sha256: str
    principal: str
    account_id: str
    db_realpath: str
    st_dev: int
    st_ino: int
    edg_genesis: str
    psh_genesis: str
    period_id: str

    def oar_g(self):
        return json.loads(self.oar_g_json)


def verify_startup_context(config, env, *, baseline_sha=None):
    """Steps 1-4 of the sealed start. Opens NO SQLite connection and writes nothing (I-G21)."""
    mode = env.get(START_MODE_ENV)
    if mode is not None and mode != SEALED_MODE:
        raise GenesisSealError(f"{START_MODE_ENV}={mode!r} rejected: normal entry points are {SEALED_MODE} only")
    anchor, signers = env.get(GENESIS_ANCHOR_ENV), env.get(ALLOWED_SIGNERS_ENV)
    if not anchor or not signers:
        raise GenesisSealError("sealed configuration absent (OAR-G and allowed_signers are required)")
    principal, data = verify_signed_document(anchor, namespace=ANCHOR_NAMESPACE, allowed_signers=signers)
    oar = parse_canonical(data, OAR_G_KIND, OAR_G_FIELDS)
    if oar["starting_equity"] != GENESIS_EQUITY or oar["account_id"] != config.account_id:
        raise GenesisSealError("OAR-G account or starting equity does not match the configuration")
    for name in ("edg_genesis", "psh_genesis", "copy_sha256"):
        if not HEX64.match(oar[name]):
            raise GenesisSealError(f"OAR-G {name} is not a SHA-256 hex digest")
    baseline = SEALED_BASELINE_SHA if baseline_sha is None else baseline_sha
    if not isinstance(baseline, str) or not SHA_PATTERN.match(baseline) or oar["x_p2"] != baseline:
        raise GenesisSealError("OAR-G x_p2 does not match the pinned baseline (X_P2 is pinned at the P4b cut)")
    if not SHA_PATTERN.match(oar["y_p2"]) or oar["y_p2"] != env.get(GIT_COMMIT_ENV):
        raise GenesisSealError(f"OAR-G y_p2 does not match {GIT_COMMIT_ENV}")
    if oar["edg_genesis"] != expected_edg_genesis(oar["account_id"], float(GENESIS_EQUITY))["edg"]:
        raise GenesisSealError("OAR-G edg_genesis differs from the recomputed expected genesis (I-G12)")
    path = Path(config.db_path)
    if path.is_symlink():
        raise GenesisSealError("the configured database path is a symlink")
    if not path.exists():
        raise GenesisSealError("sealed database file missing (a normal start never creates it)")
    if not _same_path(path, oar["db_realpath"]):
        raise GenesisSealError("the configured database path is not the anchored db_realpath")
    stat = os.stat(path)
    if (stat.st_dev, stat.st_ino) != (oar["st_dev"], oar["st_ino"]):
        raise GenesisSealError("database file identity (st_dev / st_ino) differs from OAR-G")
    if stat.st_nlink != 1:
        raise GenesisSealError("database file has more than one hard link")
    return SealedContext(data.decode("utf-8"), hashlib.sha256(data).hexdigest(), principal, oar["account_id"],
                         oar["db_realpath"], oar["st_dev"], oar["st_ino"], oar["edg_genesis"], oar["psh_genesis"],
                         oar["period_id_pending"])


def require_context(context, config):
    """The constructor's guard: a sealed runtime cannot be built without a verified context for this file."""
    if not isinstance(context, SealedContext):
        raise GenesisSealError("sealed runtime requires a verified SealedContext (steps 1-4)")
    if context.account_id != config.account_id or not _same_path(config.db_path, context.db_realpath):
        raise GenesisSealError("SealedContext does not belong to this configuration")
    return context


def identity_unchanged(context, path):
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return (stat.st_dev, stat.st_ino, stat.st_nlink) == (context.st_dev, context.st_ino, 1)


def chain_head(conn, edg_genesis):
    """``edg_after`` of the last committed REX_WRITE (its digest verified), or ``edg_genesis`` with none; None when the
    newest committed evidence is unreadable (fail closed)."""
    rows = conn.execute("SELECT payload FROM journal WHERE source='rex' AND event_type='REX_WRITE' ORDER BY id DESC")
    for (payload,) in rows:
        try:
            body = json.loads(payload)
            text = body["rex"]
            if H("V2REX/1", text) != body["rex_digest"]:
                return None
            record = json.loads(text)
        except (ValueError, KeyError, TypeError):
            return None
        if record.get("result") == "COMMITTED":
            head = record.get("edg_after")
            return head if isinstance(head, str) and HEX64.match(head) else None
    return edg_genesis


def seal_check(store, context, *, at, process_id=None):
    """Steps 2-5 (after the non-economic ``recover()``, before any economic write): one GENESIS_SEAL_CHECK row.
    Returns the payload on PASS; raises GenesisSealError after writing a FAIL."""
    account, _, _ = store.load_paper(context.account_id)
    started = store.get_state("experiment_started") == "1"
    expected = chain_head(store.db, context.edg_genesis) if started else context.edg_genesis
    observed = edg(store.db)["edg"]
    if account is None:
        reason = "genesis_sealed_account_missing"
    elif expected is None:
        reason = "chain_head_unreadable"
    elif observed != expected:
        reason = "edg_mismatch"
    else:
        reason = None
    payload = {"mode": SEALED_MODE, "oar_g_sha256": context.oar_g_sha256, "principal": context.principal,
               "period_id": context.period_id, "expected": expected, "edg_observed": observed,
               "experiment_started": started, "result": "PASS" if reason is None else "FAIL", "reason": reason,
               "process_id": process_id}
    store.event(at, None, None, GENESIS_SOURCE, SEAL_EVENT, "INFO" if reason is None else "ERROR", payload)
    if reason is not None:
        raise GenesisSealError(reason)
    return payload


def sealed_preflight_checks(config, env, *, baseline_sha=None):
    """For the preflights (R-GEN-1d): {} while the flag is OFF; otherwise the sealed context verdict, computed before
    any database open. A preflight reinforces but never replaces the constructor's own steps."""
    if not getattr(config, "v2_sealed_genesis", False):
        return {}
    try:
        verify_startup_context(config, env, baseline_sha=baseline_sha)
        return {"sealed_genesis_context": True}
    except GenesisSealError:
        return {"sealed_genesis_context": False}


__all__ = ["ALLOWED_SIGNERS_ENV", "ANCHOR_NAMESPACE", "AUTHORIZATION_FIELDS", "AUTHORIZATION_KIND",
           "AUTHORIZATION_NAMESPACE", "GENESIS_ANCHOR_ENV", "GENESIS_EQUITY", "GENESIS_SOURCE", "GenesisSealError",
           "OAR_G_FIELDS", "OAR_G_KIND", "PREPARED_EVENT", "SEALED_BASELINE_SHA", "SEAL_EVENT", "START_MODE_ENV",
           "SealedContext", "chain_head", "identity_unchanged", "parse_canonical", "require_context", "seal_check",
           "sealed_preflight_checks", "verify_signed_document", "verify_startup_context"]
