"""V2 Phase 8 / P4b freeze-amendment validator (design P8.6 3.11.1 item 1; I-C6; NC10). READ-ONLY (git plumbing).

``Y_P2`` is valid only if: its parent is ``X_P2``; ``git diff --name-only X_P2 Y_P2`` is exactly
``runtime/cloud_runner.py`` and ``EXPERIMENT_FREEZE_P2.md``; and the ``runtime/cloud_runner.py`` change is exactly
the line ``EXPERIMENT_BASELINE_SHA = "<X_P2>"`` (the single X_P2 pin used by sealed genesis). The commit signature is
checked with ``git verify-commit`` when possible; an unverifiable signature is reported NOT VERIFIED, never PASS.

Usage: python -m replay.freeze_amendment --repo . --x-p2 SHA --y-p2 SHA
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ALLOWED = ("EXPERIMENT_FREEZE_P2.md", "runtime/cloud_runner.py")
BASELINE_LINE = re.compile(r'^EXPERIMENT_BASELINE_SHA = "([0-9a-f]{40})"$')
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
WINDOWS_GIT = r"C:\Program Files\Git\cmd\git.exe"


def _git():
    found = shutil.which("git")
    if found:
        return found
    if Path(WINDOWS_GIT).is_file():
        return WINDOWS_GIT
    raise RuntimeError("git executable not found")


def _run(repo, *args):
    done = subprocess.run([_git(), "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    return done.returncode, done.stdout


def check(repo, x_p2, y_p2):
    """LOW-2 (P4b audit): both arguments must be FULL 40-hex SHAs (no ``HEAD``, ``HEAD~1``, branch or abbreviated
    names), and each must resolve to exactly that commit."""
    findings = {"x_p2_full_sha": isinstance(x_p2, str) and bool(FULL_SHA.match(x_p2)),
                "y_p2_full_sha": isinstance(y_p2, str) and bool(FULL_SHA.match(y_p2))}
    if not all(findings.values()):
        return {"result": "INVALID", "checks": findings, "changed_files": [], "commit_signature": "NOT VERIFIED"}
    for name, sha in (("x_p2", x_p2), ("y_p2", y_p2)):
        code, resolved = _run(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        findings[f"{name}_resolves_to_itself"] = code == 0 and resolved.strip() == sha
    code, parent = _run(repo, "rev-parse", f"{y_p2}^")
    findings["parent_is_x_p2"] = code == 0 and parent.strip() == x_p2
    code, names = _run(repo, "diff", "--name-only", x_p2, y_p2)
    changed = tuple(sorted(n.strip() for n in names.splitlines() if n.strip()))
    findings["touches_only_allowed_files"] = code == 0 and changed == ALLOWED
    code, diff = _run(repo, "diff", "-U0", x_p2, y_p2, "--", "runtime/cloud_runner.py")
    body = [line for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    added = [BASELINE_LINE.match(line[1:]) for line in body if line.startswith("+")]
    removed = [BASELINE_LINE.match(line[1:]) for line in body if line.startswith("-")]
    findings["cloud_runner_changes_only_the_baseline_pin"] = (
        code == 0 and len(added) == 1 and len(removed) == 1 and all(added) and all(removed)
        and added[0].group(1) == x_p2)
    code, _ = _run(repo, "verify-commit", y_p2)
    signature = "PASS" if code == 0 else "NOT VERIFIED"
    valid = all(findings.values())
    return {"result": "VALID" if valid else "INVALID", "checks": findings, "changed_files": list(changed),
            "commit_signature": signature}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate the X_P2 -> Y_P2 freeze amendment (I-C6).")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--x-p2", required=True)
    parser.add_argument("--y-p2", required=True)
    args = parser.parse_args(argv)
    report = check(args.repo, args.x_p2, args.y_p2)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["result"] == "VALID" else 2


if __name__ == "__main__":
    sys.exit(main())
