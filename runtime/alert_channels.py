"""V2 Phase 9: alert delivery channels — Gmail SMTP email and Slack webhook.

A channel either returns normally or raises ``AlertChannelError`` carrying only a typed code; the code never
includes the app password, webhook URL, recipient list or provider response body. ``retryable`` tells the
dispatcher whether a bounded retry can help (configuration and authentication failures cannot).
"""
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import os
import smtplib
import ssl

from runtime.alert_render import render_html, render_text, subject
from runtime.notifications import SlackNotificationError, _slack_post, send_slack

DEFAULT_ALERT_EMAIL = "yefersontejeda@gmail.com"
GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465
NON_RETRYABLE = frozenset({"NOT_CONFIGURED", "AUTH_ERROR", "REJECTED"})


class AlertChannelError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.retryable = code not in NON_RETRYABLE


class GmailEmailChannel:
    name = "email"

    def __init__(self, *, password=None, sender=None, recipient=None, smtp_factory=None, timeout=15,
                 environ=None):
        env = os.environ if environ is None else environ
        self.password = password if password is not None else env.get("GMAIL_APP_PASSWORD")
        self.sender = sender or env.get("AI_FLOOR_ALERT_EMAIL_FROM") or DEFAULT_ALERT_EMAIL
        self.recipient = recipient or env.get("AI_FLOOR_ALERT_EMAIL_TO") or DEFAULT_ALERT_EMAIL
        self.smtp_factory = smtp_factory  # resolved at send time so tests can patch smtplib.SMTP_SSL
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("invalid SMTP timeout")

    @property
    def configured(self):
        return bool(self.password)

    def message(self, event):
        msg = EmailMessage()
        msg["Subject"] = subject(event)
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(idstring=event.event_id, domain="ai-trading-floor.local")
        msg["X-AI-Floor-Event-ID"] = event.event_id
        msg.set_content(render_text(event))
        msg.add_alternative(render_html(event), subtype="html")
        return msg

    def send(self, event):
        if not self.configured:
            raise AlertChannelError("NOT_CONFIGURED")
        msg = self.message(event)
        factory = self.smtp_factory or smtplib.SMTP_SSL
        try:
            with factory(GMAIL_HOST, GMAIL_PORT, timeout=self.timeout,
                         context=ssl.create_default_context()) as smtp:
                smtp.login(self.sender, self.password.replace(" ", ""))
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise AlertChannelError("AUTH_ERROR") from None
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise AlertChannelError("REJECTED") from None
        except (smtplib.SMTPException, OSError):  # includes timeouts and connection errors
            raise AlertChannelError("CONNECTION_ERROR") from None


class SlackAlertChannel:
    """Reuses the existing Slack transport and typed errors from ``runtime.notifications``."""
    name = "slack"

    def __init__(self, *, webhook_url=None, transport=None, timeout=10, environ=None):
        env = os.environ if environ is None else environ
        self.webhook_url = webhook_url if webhook_url is not None else env.get("SLACK_WEBHOOK_URL")
        self.transport = transport or _slack_post
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("invalid Slack timeout")

    @property
    def configured(self):
        return bool(self.webhook_url)

    def send(self, event):
        if not self.configured:
            raise AlertChannelError("NOT_CONFIGURED")
        try:
            send_slack(self.transport, self.webhook_url, {"text": render_text(event)}, self.timeout)
        except SlackNotificationError as exc:
            raise AlertChannelError(str(exc)) from None


__all__ = ["AlertChannelError", "DEFAULT_ALERT_EMAIL", "GmailEmailChannel", "SlackAlertChannel"]
