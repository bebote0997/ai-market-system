"""V2 Phase 9: alert delivery channels — Gmail SMTP email and Slack webhook.

A channel either returns normally or raises ``AlertChannelError`` carrying only a typed code; the code never
includes the app password, webhook URL, recipient list or provider response body. ``retryable`` tells the
dispatcher whether a bounded retry can help (configuration, authentication and permanent 5xx failures cannot;
temporary 4xx SMTP replies can).
"""
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import logging
import os
import smtplib
import ssl

from runtime.alert_chart import chart_png
from runtime.alert_render import render_html, render_text, subject
from runtime.notifications import SlackNotificationError, _slack_post, send_slack

LOG = logging.getLogger(__name__)
GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465
NON_RETRYABLE = frozenset({"NOT_CONFIGURED", "AUTH_ERROR", "REJECTED", "INVALID_WEBHOOK_URL"})


class AlertChannelError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.retryable = code not in NON_RETRYABLE


class GmailEmailChannel:
    name = "email"

    def __init__(self, *, password=None, sender=None, recipient=None, smtp_factory=None, timeout=10,
                 environ=None):
        env = os.environ if environ is None else environ
        self.password = password if password is not None else env.get("GMAIL_APP_PASSWORD")
        # No built-in addresses: sender and recipient come only from arguments or the environment.
        self.sender = sender or env.get("AI_FLOOR_ALERT_EMAIL_FROM") or None
        self.recipient = recipient or env.get("AI_FLOOR_ALERT_EMAIL_TO") or None
        self.smtp_factory = smtp_factory  # resolved at send time so tests can patch smtplib.SMTP_SSL
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 10:
            raise ValueError("invalid SMTP timeout")

    @property
    def missing(self):
        """Names (never values) of the settings still required before email can be sent."""
        return tuple(name for name, value in (("GMAIL_APP_PASSWORD", self.password),
                                              ("AI_FLOOR_ALERT_EMAIL_FROM", self.sender),
                                              ("AI_FLOOR_ALERT_EMAIL_TO", self.recipient)) if not value)

    @property
    def configured(self):
        return not self.missing

    def message(self, event, attachments=()):
        msg = EmailMessage()
        msg["Subject"] = subject(event)
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(idstring=event.event_id, domain="ai-trading-floor.local")
        msg["X-AI-Floor-Event-ID"] = event.event_id
        msg.set_content(render_text(event))
        png = chart_png(event.closes) if event.kind.trade else None
        cid = make_msgid(domain="ai-trading-floor.local") if png else None
        msg.add_alternative(render_html(event, chart_cid=cid[1:-1] if cid else None), subtype="html")
        if png:
            msg.get_payload()[-1].add_related(png, maintype="image", subtype="png", cid=cid,
                                             disposition="inline", filename="chart.png")
        for filename, content, maintype, subtype in attachments:
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
        return msg

    def rendered_message(self, subject_line, text, html, *, event_id):
        """Floor-style email (``runtime.trade_alerts``): plain-text part + dark HTML alternative."""
        msg = EmailMessage()
        msg["Subject"] = subject_line
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(domain="ai-trading-floor.local")
        msg["X-AI-Floor-Event-ID"] = str(event_id)
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        return msg

    def send_rendered(self, subject_line, text, html, *, event_id):
        if not self.configured:
            raise AlertChannelError("NOT_CONFIGURED")
        self._smtp_send(self.rendered_message(subject_line, text, html, event_id=event_id))

    def send(self, event, attachments=()):
        if not self.configured:
            raise AlertChannelError("NOT_CONFIGURED")
        self._smtp_send(self.message(event, attachments=attachments))

    def _smtp_send(self, msg):
        factory = self.smtp_factory or smtplib.SMTP_SSL
        try:
            smtp = factory(GMAIL_HOST, GMAIL_PORT, timeout=self.timeout, context=ssl.create_default_context())
        except (smtplib.SMTPException, OSError) as exc:
            raise AlertChannelError(classify_smtp_error(exc)) from None
        try:
            smtp.login(self.sender, self.password.replace(" ", ""))
            smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise AlertChannelError(classify_smtp_error(exc)) from None
        finally:
            # Cleanup is separate from delivery: once send_message returned the message is accepted (SENT), so a
            # failing QUIT/close is only logged and never turns the send into a retry (duplicate email).
            _close_quietly(smtp)


def classify_smtp_error(exc):
    """Typed code for an SMTP failure: 4xx is temporary (retryable), 5xx permanent (REJECTED / AUTH_ERROR)."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return "TEMPORARY_FAILURE" if codes and all(400 <= code < 500 for code in codes) else "REJECTED"
    code = getattr(exc, "smtp_code", None) if isinstance(exc, smtplib.SMTPResponseException) else None
    if isinstance(code, int) and 400 <= code < 500:
        return "TEMPORARY_FAILURE"
    if isinstance(code, int) and 500 <= code < 600:
        return "AUTH_ERROR" if isinstance(exc, smtplib.SMTPAuthenticationError) else "REJECTED"
    return "CONNECTION_ERROR"  # disconnects, timeouts, DNS and responses without a 4xx/5xx code


def _close_quietly(smtp):
    try:
        smtp.quit()
    except Exception as exc:  # noqa: BLE001 - cleanup only; the delivery outcome is already decided
        LOG.warning("component=alerts event=SMTP_CLEANUP_FAILED error_type=%s", type(exc).__name__)
        try:
            smtp.close()
        except Exception:  # noqa: BLE001
            pass


class SlackAlertChannel:
    """Reuses the existing Slack transport and typed errors from ``runtime.notifications``."""
    name = "slack"

    def __init__(self, *, webhook_url=None, transport=None, timeout=10, environ=None):
        env = os.environ if environ is None else environ
        self.webhook_url = webhook_url if webhook_url is not None else env.get("SLACK_WEBHOOK_URL")
        self.transport = transport or _slack_post
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 10:
            raise ValueError("invalid Slack timeout")

    @property
    def configured(self):
        return bool(self.webhook_url)

    def send(self, event):
        if not self.configured:
            raise AlertChannelError("NOT_CONFIGURED")
        try:
            send_slack(self.transport, self.webhook_url, slack_payload(event), self.timeout)
        except SlackNotificationError as exc:
            raise AlertChannelError(str(exc)) from None


def slack_payload(event):
    """Compact Floor-style Slack message (DEC-ALERTS-REDESIGN-1): header + one detail line, never a field dump."""
    from runtime.trade_alerts import system_slack_payload
    label = event.event_type if not event.kind.trade or event.is_test else event.kind.label.capitalize()
    return system_slack_payload(label, event.symbol, event.summary)


__all__ = ["AlertChannelError", "GmailEmailChannel", "SlackAlertChannel", "slack_payload",
           "classify_smtp_error"]
