"""Read-only Exness connectivity check. Prints account, instrument spec and the last tick. Never submits."""
import argparse
import json
import os

from execution.exness_broker import broker_from_env


def report(env=None, *, transport=None):
    env = os.environ if env is None else env
    broker = broker_from_env(env, transport=transport)
    before = list(getattr(transport, "commands", []))
    view = broker.connectivity_report()
    if getattr(transport, "commands", before) != before:
        raise RuntimeError("connectivity check submitted a command")
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read Exness account, instrument spec and last tick. Sends nothing.")
    parser.add_argument("--resume", action="store_true",
                        help="Clear a brake only when AI_FLOOR_EXNESS_RESUME is exactly RESUME_EXNESS. Sends nothing.")
    args = parser.parse_args(argv)
    if args.resume:
        from execution.exness_brakes import RESUME_PHRASE, ExnessBrakes, flag_store_from_env
        cleared = ExnessBrakes(flag_store_from_env(os.environ),).clear_halt(os.environ.get("AI_FLOOR_EXNESS_RESUME"))
        print(json.dumps({"resumed": cleared, "phrase_ok": os.environ.get("AI_FLOOR_EXNESS_RESUME") == RESUME_PHRASE},
                         sort_keys=True))
        return 0 if cleared else 1
    try:
        view = report()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(view, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
