"""Send ONE labelled TEST alert through the Phase 9 alert path (F09-T18). Manual owner action only.

    python -m runtime.alert_test --slack     # SLACK_WEBHOOK_URL
    python -m runtime.alert_test --email     # GMAIL_APP_PASSWORD (+ AI_FLOOR_ALERT_EMAIL_FROM/_TO)

Running the command is the explicit consent to send, so it does not read ``AI_FLOOR_ALERTS_ENABLED`` (the
runtime hook stays OFF). Prints only typed statuses — never the webhook URL, password or recipients. Touches no
trading state. Exit code 0 only when every selected channel reports DELIVERED.
"""
import argparse
from datetime import datetime, timezone
import json
import sys
import uuid

from runtime.alert_catalog import make_test_alert
from runtime.alert_channels import GmailEmailChannel, SlackAlertChannel
from runtime.alert_dispatch import AlertDispatcher


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m runtime.alert_test", description=__doc__.splitlines()[0])
    parser.add_argument("--slack", action="store_true", help="send the test alert to Slack")
    parser.add_argument("--email", action="store_true", help="send the test alert by Gmail SMTP")
    args = parser.parse_args(argv)
    channels = ([SlackAlertChannel()] if args.slack else []) + ([GmailEmailChannel()] if args.email else [])
    if not channels:
        parser.error("choose at least one of --slack / --email")
    at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    event = make_test_alert(at, nonce=uuid.uuid4().hex)
    alerts = AlertDispatcher(channels, enabled=True, max_attempts=1)
    result = alerts.dispatch(event)
    print(json.dumps({"event_id": event.event_id, "result": result,
                      "ledger": alerts.ledger.status(event.event_id)}, sort_keys=True))  # typed codes only
    return 0 if result and set(result.values()) == {"DELIVERED"} else 1


if __name__ == "__main__":
    sys.exit(main())
