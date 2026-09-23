"""Outbound mail for password-reset links.

The only email LANA ever sends. A stdlib ``smtplib`` wrapper rather than a
new dependency — the same call this project makes for password hashing
(``hashlib.scrypt`` over a third-party library): one email a day, from one
feature, does not justify carrying an SDK.

Failures here are logged and swallowed, never raised into the request path.
``/auth/forgot-password`` always returns the same generic response whether or
not a user exists *and* whether or not the send actually succeeded — a
client's mail server being briefly unreachable should not turn into a 500 for
someone trying to recover their account, and should not tell an attacker
anything either.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import SmtpConfig

logger = logging.getLogger("lana.mailer")


def send_password_reset_email(smtp: SmtpConfig, to_address: str, reset_link: str) -> bool:
    """Send the reset email. Returns whether it was actually sent.

    The return value is for logging/tests, not for the caller to branch
    response behaviour on — see the module docstring.
    """
    if not smtp.configured:
        logger.warning(
            "password reset requested but SMTP is not configured "
            "(set SMTP_HOST, SMTP_FROM and LANA_PUBLIC_URL) — no email sent"
        )
        return False

    message = EmailMessage()
    message["Subject"] = "Reset your LANA password"
    message["From"] = smtp.from_address
    message["To"] = to_address
    message.set_content(
        "A password reset was requested for your LANA account.\n\n"
        f"Reset it here: {reset_link}\n\n"
        "This link expires in 30 minutes and can be used once. If you did "
        "not request this, you can ignore this email — your password has "
        "not been changed."
    )

    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=10) as client:
            if smtp.use_tls:
                client.starttls()
            if smtp.username:
                client.login(smtp.username, smtp.password)
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("failed to send password-reset email")
        return False
