"""python -m runtime [--once SYMBOL | --recover-only]. PAPER only."""
import argparse
import logging
from threading import Event

from runtime.config import RuntimeConfig
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime


def main():
    parser = argparse.ArgumentParser(description="AI Trading Floor PAPER runtime")
    parser.add_argument("--once", choices=("XAUUSD", "NAS100", "EURUSD"))
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runtime = OperationalRuntime(RuntimeConfig.from_env())
    stop = Event()
    try:
        if args.recover_only:
            return
        if args.once:
            from runtime.scheduler import slot_at
            print(runtime.run_cycle(args.once, slot_at(runtime.clock(), runtime.config.cadence_minutes)))
            return
        scheduler = Scheduler(runtime, runtime.clock)
        try:
            while not stop.wait(1):
                scheduler.tick()
        except KeyboardInterrupt:
            stop.set()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
