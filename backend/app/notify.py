"""Email delivery for weekly summaries and alerts. Does nothing unless SMTP is configured."""

import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> bool:
    if not enabled() or not to:
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.warning("Email to user failed: %s", e)
        return False
    return True
