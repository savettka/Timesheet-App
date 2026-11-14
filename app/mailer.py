"""Sending email: sign-in codes, and the weekly report.

PythonAnywhere's free tier blocks outbound SMTP except to Google's mail
servers, so the defaults here point at Gmail. Any provider works on a paid
plan by overriding the environment variables.

Nothing is configured by default: when SMTP settings are absent the app
simply doesn't offer code-based sign-in, rather than showing an option that
silently fails.
"""

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

SMTP_TIMEOUT_SECONDS = 15


def is_configured(app=None):
    app = app or current_app
    return bool(
        app.config.get("SMTP_HOST")
        and app.config.get("SMTP_USERNAME")
        and app.config.get("SMTP_PASSWORD")
    )


def _build_message(to_address, code, minutes):
    app = current_app
    sender = app.config.get("MAIL_FROM") or app.config["SMTP_USERNAME"]

    message = EmailMessage()
    message["Subject"] = f"{code} is your STM sign-in code"
    message["From"] = sender
    message["To"] = to_address
    message.set_content(
        f"Your STM sign-in code is:\n\n"
        f"    {code}\n\n"
        f"It expires in {minutes} minutes and can only be used once.\n\n"
        f"If you didn't try to sign in, you can ignore this email — "
        f"someone entered your address by mistake, and without this code "
        f"they can't get in.\n"
    )
    return message


def _deliver(message, what):
    """Send a built message through the configured SMTP server. Returns True
    on success; failures are logged and reported as False rather than
    raised, so a mail outage shows a clear message instead of a 500 page."""
    app = current_app
    if not is_configured(app):
        app.logger.warning("%s, but SMTP is not configured.", what)
        return False

    host = app.config["SMTP_HOST"]
    port = int(app.config.get("SMTP_PORT", 587))
    username = app.config["SMTP_USERNAME"]
    password = app.config["SMTP_PASSWORD"]
    use_ssl = bool(app.config.get("SMTP_USE_SSL"))
    context = ssl.create_default_context()

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS,
                                  context=context) as server:
                server.login(username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                server.starttls(context=context)  # encrypt before sending credentials
                server.ehlo()
                server.login(username, password)
                server.send_message(message)
        return True
    except Exception:
        # The address is deliberately not logged alongside the failure, to keep
        # user emails out of the server log.
        app.logger.exception("Failed to send: %s.", what)
        return False


def send_login_code(to_address, code, minutes):
    """Email a sign-in code. Returns True on success."""
    if not is_configured():
        return _deliver(None, "a sign-in code email")
    return _deliver(_build_message(to_address, code, minutes), "a sign-in code email")


def send_email(to_address, subject, text, html=None):
    """Email anything else -- the weekly report -- as plain text, with an
    HTML version beside it when given. Returns True on success."""
    if not is_configured():
        return _deliver(None, "an email")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config.get("MAIL_FROM") or current_app.config["SMTP_USERNAME"]
    message["To"] = to_address
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return _deliver(message, "an email")
