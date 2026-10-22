"""OANDA demo daily summary by email (DEC-ALERTS-REDESIGN-1), sent by the durable Slack sink after Slack succeeded.

Best effort and once: OFF unless ``AI_FLOOR_ALERTS_ENABLED`` is exactly ``1`` and Gmail is configured; failures are
logged by typed code only and never re-send the Slack summary. Never touches trading state.
"""
import logging

from runtime.alert_channels import GmailEmailChannel
from runtime.alert_dispatch import alerts_enabled
from runtime.trade_alerts import daily_email

LOG = logging.getLogger(__name__)


def send_daily_email(alert, event_id, environ=None):
    try:
        if not alerts_enabled(environ):
            return False
        channel = GmailEmailChannel(environ=environ)
        if not channel.configured:
            return False
        channel.send_rendered(*daily_email(alert), event_id=event_id)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("component=alerts event=DAILY_SUMMARY_EMAIL_FAILED error_type=%s",
                    getattr(exc, "code", None) or type(exc).__name__)
        return False


__all__ = ["send_daily_email"]
