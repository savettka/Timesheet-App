"""Email: through Resend's web API when its key is set (it works on
PythonAnywhere's free plan, where only Gmail's SMTP is let out), otherwise
through SMTP as before. Nothing is really sent here."""
import io
import json
import logging
import urllib.error

import pytest

from app import mailer


class Answer:
    def read(self):
        return b'{"id": "test"}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def resend(app, monkeypatch):
    app.config.update(RESEND_API_KEY="re_test", MAIL_FROM="STM <stm@example.org>",
                      MAIL_REPLY_TO="me@example.org")
    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append(request)
        return Answer()

    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_a_key_and_a_sender_count_as_set_up(app, resend):
    assert mailer.is_configured(app)
    app.config["MAIL_FROM"] = None
    assert not mailer.is_configured(app)


def test_an_email_goes_through_resend(app, resend):
    with app.app_context():
        assert mailer.send_email("someone@example.org", "Hello", "Plain words", "<p>Rich</p>")
    request = resend[0]
    assert request.full_url == "https://api.resend.com/emails" and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer re_test"
    assert json.loads(request.data) == {
        "from": "STM <stm@example.org>", "to": ["someone@example.org"], "subject": "Hello",
        "text": "Plain words", "html": "<p>Rich</p>", "reply_to": "me@example.org",
    }


def test_a_sign_in_code_goes_through_resend(app, resend):
    with app.app_context():
        assert mailer.send_login_code("someone@example.org", "123456", 10)
    body = json.loads(resend[0].data)
    assert body["subject"] == "123456 is your STM sign-in code" and "123456" in body["text"]
    assert "html" not in body


def test_a_refusal_is_false_and_keeps_the_address_out_of_the_log(app, resend, monkeypatch, caplog):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 422, "Unprocessable", {},
            io.BytesIO(b'{"name": "validation_error", "message": "Invalid to: someone@example.org"}'))

    monkeypatch.setattr(mailer.urllib.request, "urlopen", refuse)
    with app.app_context(), caplog.at_level(logging.ERROR):
        assert mailer.send_email("someone@example.org", "Hello", "Plain") is False
    assert "422" in caplog.text and "validation_error" in caplog.text
    assert "someone@example.org" not in caplog.text


def test_sign_in_by_code_is_offered_with_resend(app, user_id, resend):
    page = app.test_client().get("/login").get_data(as_text=True)
    assert "Email me a sign-in code instead" in page


def test_without_a_key_smtp_as_before_with_the_reply_address(app, monkeypatch):
    app.config.update(SMTP_USERNAME="stm@example.org", SMTP_PASSWORD="test-only",
                      MAIL_REPLY_TO="me@example.org")
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no web call"))
    with app.app_context():
        assert mailer.send_email("someone@example.org", "Hello", "Plain")
    assert sent[0]["Reply-To"] == "me@example.org" and sent[0]["From"] == "stm@example.org"
