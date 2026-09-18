"""Single continuous PAPER scheduler, disabled until cloud activation."""
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import time

from runtime.config import RuntimeConfig
from runtime.cloud import cloud_preflight
from runtime.demo_runner import DemoRunner
from runtime.notifications import SlackNotificationSink


def main():
    if os.environ.get("RENDER") != "true" or os.environ.get("AI_FLOOR_CLOUD_RUNNER") != "1":
        raise RuntimeError("cloud runner not activated")
    config = RuntimeConfig.from_env()
    if not config.scheduler_enabled:
        raise RuntimeError("scheduler or macro provider not configured")
    from dataclasses import replace
    activation_env = dict(os.environ, AI_FLOOR_CLOUD_RUNNER="0", AI_FLOOR_SCHEDULER="0")
    if not cloud_preflight(replace(config, scheduler_enabled=False), env=activation_env).experiment_ready:
        raise RuntimeError("experiment activation preflight not ready")
    import fcntl
    lock_path = Path(config.db_path).with_suffix(".runner.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("scheduler authority already active") from None
        runner = DemoRunner(config, notification_sink=SlackNotificationSink())
        try:
            while not stop:
                runner.tick()
                runner.daily_summary(datetime.now(timezone.utc))
                for _ in range(30):
                    if stop:
                        break
                    time.sleep(1)
        finally:
            runner.close()


if __name__ == "__main__":
    main()
