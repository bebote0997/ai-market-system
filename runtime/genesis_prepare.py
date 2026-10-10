"""V2 Phase 8 / P2b GENESIS_PREPARATION tool (design P8.6 5.1.3.2 G0-G1, start-mode contract; R-GEN-1c).

The ONLY creator of a sealed PAPER account. Selected exclusively by invoking this module; no normal entry point imports
it and no environment variable or default selects it (R-GEN-1e, inventory-tested).

    python -m runtime.genesis_prepare --authorization AUTH.json --allowed-signers FILE --db NEW_PATH

- AUTH.json is an Owner-signed ``V2_GENESIS_AUTHORIZATION/1`` (``AUTH.json.sig``, namespace
  ``v2-genesis-authorization``), canonical JSON: ``period_id``, ``db_realpath`` (the target), ``x_p2``, ``y_p2``,
  ``starting_equity`` = 10000, ``account_id`` and ``not_after_utc`` (short lifetime).
- The target is created EXCLUSIVELY (``O_CREAT | O_EXCL``): an existing file is refused, so a sealed file is never
  re-prepared; a file left by a crash is never reused (the Owner discards it and prepares a new genesis).
- The account (equity 10000, PAPER only) and ``GENESIS_PREPARED {authorization_sha256, ...}`` are written in ONE
  transaction. No runtime is constructed, no recovery, no experiment start, no cycle.
- It never signs anything: OAR-G is computed later from a copy (``replay.genesis_anchor``) and signed by the Owner.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from runtime.genesis import (AUTHORIZATION_FIELDS, AUTHORIZATION_KIND, AUTHORIZATION_NAMESPACE, GENESIS_EQUITY,
                             GENESIS_SOURCE, PREPARED_EVENT, SHA_PATTERN, GenesisSealError, parse_canonical,
                             verify_signed_document)
from storage.database import Store
from storage.economic_digest import edg, expected_edg_genesis, psh

TOOL_VERSION = "V2_P2B_GENESIS_PREPARE/1"


def _parse_utc(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:
        raise GenesisSealError("not_after_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def verify_authorization(authorization_path, allowed_signers, target, *, now=None):
    """The signed authorization for ``target``; raises GenesisSealError otherwise."""
    principal, data = verify_signed_document(authorization_path, namespace=AUTHORIZATION_NAMESPACE,
                                             allowed_signers=allowed_signers)
    auth = parse_canonical(data, AUTHORIZATION_KIND, AUTHORIZATION_FIELDS)
    now = now or datetime.now(timezone.utc)
    if _parse_utc(auth["not_after_utc"]) <= now:
        raise GenesisSealError("genesis authorization expired")
    if auth["starting_equity"] != GENESIS_EQUITY:
        raise GenesisSealError("genesis authorization starting_equity must be exactly 10000")
    if not auth["account_id"] or not auth["period_id"]:
        raise GenesisSealError("genesis authorization needs account_id and period_id")
    for name in ("x_p2", "y_p2"):
        if not SHA_PATTERN.match(auth[name]):
            raise GenesisSealError(f"genesis authorization {name} is not a commit SHA")
    if os.path.normcase(os.path.realpath(target)) != os.path.normcase(auth["db_realpath"]):
        raise GenesisSealError("genesis authorization is bound to another target path")
    return principal, auth, hashlib.sha256(data).hexdigest()


def prepare(authorization_path, allowed_signers, target, *, now=None):
    """G0-G1. Returns the summary (expected and observed genesis digests)."""
    target = Path(target)
    principal, auth, auth_sha256 = verify_authorization(authorization_path, allowed_signers, target, now=now)
    if target.is_symlink() or target.exists():
        raise GenesisSealError("target exists: genesis preparation is create-exclusive")
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise GenesisSealError("target exists: genesis preparation is create-exclusive") from None
    os.close(fd)
    at = now or datetime.now(timezone.utc)
    store = Store(target)  # G0: schema 3 in the exclusively created, empty file; no economic rows
    try:
        account = PaperAccount("1.0", auth["account_id"], float(GENESIS_EQUITY), float(GENESIS_EQUITY),
                               float(GENESIS_EQUITY))
        broker = PaperBroker(account)
        broker._event(at, None, None, GENESIS_SOURCE, PREPARED_EVENT,
                      {"tool_version": TOOL_VERSION, "authorization_sha256": auth_sha256, "principal": principal,
                       "period_id": auth["period_id"], "account_id": auth["account_id"],
                       "starting_equity": GENESIS_EQUITY, "x_p2": auth["x_p2"], "y_p2": auth["y_p2"],
                       "edg_genesis_expected": expected_edg_genesis(auth["account_id"], float(GENESIS_EQUITY))["edg"]})
        store.save_paper(broker)  # G1: the account row and GENESIS_PREPARED in one transaction
        observed = edg(store.db)["edg"]
        state = psh(store.paper_state(*store.load_paper(auth["account_id"])))
    finally:
        store.close()
    expected = expected_edg_genesis(auth["account_id"], float(GENESIS_EQUITY))["edg"]
    return {"tool_version": TOOL_VERSION, "db_realpath": os.path.realpath(target), "account_id": auth["account_id"],
            "period_id": auth["period_id"], "authorization_sha256": auth_sha256, "edg_genesis": observed,
            "edg_genesis_expected": expected, "psh_genesis": state, "matches_expected": observed == expected}


def main(argv=None):
    parser = argparse.ArgumentParser(description="GENESIS_PREPARATION: create one sealed PAPER account (10000).")
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--allowed-signers", required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    try:
        summary = prepare(args.authorization, args.allowed_signers, args.db)
    except GenesisSealError as exc:
        print(json.dumps({"result": "REFUSED", "reason": str(exc)}))
        return 2
    print(json.dumps({"result": "PREPARED", **summary}, indent=2, sort_keys=True))
    return 0 if summary["matches_expected"] else 3


if __name__ == "__main__":
    sys.exit(main())
