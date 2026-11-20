"""Sending email: sign-in codes, and the weekly report.

Two ways out, picked by what's configured:

- Resend's web API, when RESEND_API_KEY and MAIL_FROM are set. It goes over
  HTTPS, which PythonAnywhere's free tier allows to Resend, so mail can come
  from your own domain there.
- SMTP otherwise. The free tier blocks outbound SMTP except to Google's mail
  servers, so the defaults point at Gmail; any provider works on a paid plan.

MAIL_REPLY_TO, when set, is where replies go (both ways).

Nothing is configured by default: with neither set up the app simply doesn't
offer code-based sign-in, rather than showing an option that silently fails.
"""

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from flask import current_app

SEND_TIMEOUT_SECONDS = 15
RESEND_URL = "https://api.resend.com/emails"


def _uses_resend(app):
    return bool(app.config.get("RESEND_API_KEY") and app.config.get("MAIL_FROM"))


def is_configured(app=None):
    app = app or current_app
    return _uses_resend(app) or bool(
        app.config.get("SMTP_HOST")
        and app.config.get("SMTP_USERNAME")
        and app.config.get("SMTP_PASSWORD")
    )


def _send_resend(app, to_address, subject, text, html):
    body = {"from": app.config["MAIL_FROM"], "to": [to_address], "subject": subject, "text": text}
    if html:
        body["html"] = html
    if app.config.get("MAIL_REPLY_TO"):
        body["reply_to"] = app.config["MAIL_REPLY_TO"]
    request = urllib.request.Request(
        RESEND_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={
            "Authorization": f"Bearer {app.config['RESEND_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": "STM",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as answer:
            answer.read()
    except urllib.error.HTTPError as error:
        # Resend's own words can repeat the address; only its code and the
        # kind of error go in the log.
        try:
            kind = json.loads(error.read() or b"{}").get("name", "")
        except ValueError:
            kind = ""
        raise RuntimeError(f"Resend answered {error.code} {kind}".strip()) from None


def _send_smtp(app, to_address, subject, text, html):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config.get("MAIL_FROM") or app.config["SMTP_USERNAME"]
    message["To"] = to_address
    if app.config.get("MAIL_REPLY_TO"):
        message["Reply-To"] = app.config["MAIL_REPLY_TO"]
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    host = app.config["SMTP_HOST"]
    port = int(app.config.get("SMTP_PORT", 587))
    username = app.config["SMTP_USERNAME"]
    password = app.config["SMTP_PASSWORD"]
    context = ssl.create_default_context()
    if app.config.get("SMTP_USE_SSL"):
        with smtplib.SMTP_SSL(host, port, timeout=SEND_TIMEOUT_SECONDS, context=context) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=SEND_TIMEOUT_SECONDS) as server:
            server.ehlo()
            server.starttls(context=context)  # encrypt before sending credentials
            server.ehlo()
            server.login(username, password)
            server.send_message(message)


def _send(to_address, subject, text, html, what):
    """Send one email the configured way. Returns True on success; failures
    are logged and reported as False rather than raised, so a mail outage
    shows a clear message instead of a 500 page."""
    app = current_app
    if not is_configured(app):
        app.logger.warning("%s, but email is not configured.", what)
        return False
    try:
        if _uses_resend(app):
            _send_resend(app, to_address, subject, text, html)
        else:
            _send_smtp(app, to_address, subject, text, html)
        return True
    except Exception:
        # The address is deliberately not logged alongside the failure, to keep
        # user emails out of the server log.
        app.logger.exception("Failed to send: %s.", what)
        return False


def send_login_code(to_address, code, minutes):
    """Email a sign-in code. Returns True on success."""
    text = (
        f"Your STM sign-in code is:\n\n"
        f"    {code}\n\n"
        f"It expires in {minutes} minutes and can only be used once.\n\n"
        f"If you didn't try to sign in, you can ignore this email — "
        f"someone entered your address by mistake, and without this code "
        f"they can't get in.\n"
    )
    return _send(to_address, f"{code} is your STM sign-in code", text, None, "a sign-in code email")


def send_email(to_address, subject, text, html=None):
    """Email anything else -- the weekly report -- as plain text, with an
    HTML version beside it when given. Returns True on success."""
    return _send(to_address, subject, text, html, "an email")
