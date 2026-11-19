"""Your own domain through a proxy in front (app/proxy.py): with the shared
secret right, STM takes the proxy's word for the visitor's address and the
domain; otherwise its headers count for nothing."""
import pytest

import app.auth as auth

SECRET = "proxy-test-secret"


def through(ip="203.0.113.5", secret=SECRET, host="stm.example.org"):
    return {"X-STM-Proxy": secret, "X-STM-Client-IP": ip, "X-STM-Host": host, "X-STM-Proto": "https"}


@pytest.fixture
def proxied(app):
    app.config["PROXY_SECRET"] = SECRET
    return app


@pytest.fixture
def no_failures(monkeypatch):
    monkeypatch.setattr(auth, "_login_failures", {})


def email_link(client, headers):
    page = client.get("/reports/weekly-email", headers=headers).get_data(as_text=True)
    return "https://stm.example.org/reports" in page, "http://localhost/reports" in page


def test_links_use_the_domain_asked_for(proxied, client):
    assert email_link(client, through()) == (True, False)
    assert email_link(client, {}) == (False, True)


def test_a_wrong_secret_or_none_set_changes_nothing(app, client):
    assert email_link(client, through()) == (False, True)  # STM_PROXY_SECRET unset
    app.config["PROXY_SECRET"] = SECRET
    assert email_link(client, through(secret="guess")) == (False, True)
    assert email_link(client, through(secret="")) == (False, True)


def test_odd_values_are_ignored(proxied, client):
    page = client.get("/reports/weekly-email", headers=through(host="evil.example/x y")).get_data(as_text=True)
    assert "evil.example" not in page and "https://localhost/reports" in page
    assert email_link(client, through(ip="not-an-address")) == (True, False)


def test_the_old_address_sends_pages_on(app, client):
    app.config["PUBLIC_URL"] = "https://stm.example.org/"
    moved = client.get("/timesheet/week?date=2026-11-10")
    assert moved.status_code == 302
    assert moved.headers["Location"] == "https://stm.example.org/timesheet/week?date=2026-11-10"
    assert client.head("/reports").headers["Location"] == "https://stm.example.org/reports"


def test_what_stays_at_the_old_address(app, client):
    app.config["PUBLIC_URL"] = "https://stm.example.org"
    app.config["PROXY_SECRET"] = SECRET
    assert client.get("/reports", headers=through()).status_code == 200  # the proxy itself
    assert client.get("/reports", headers=through(secret="stale")).status_code == 200  # no loop
    assert "example.org" not in client.get("/calendar/leave.ics?key=nope").headers.get("Location", "")
    assert client.get("/deploy/github").status_code == 405
    posted = app.test_client().post("/login", data={"username": "x", "password": "y"})
    assert "example.org" not in posted.headers.get("Location", "")


def test_unset_nothing_moves(client):
    assert client.get("/reports").status_code == 200


def test_wrong_passwords_count_per_visitor(proxied, user_id, no_failures):
    visitor = proxied.test_client()
    for _ in range(auth.LOGIN_FAILURE_LIMIT):
        visitor.post("/login", data={"username": "tester", "password": "wrong"}, headers=through("203.0.113.5"))
    blocked = visitor.post("/login", data={"username": "tester", "password": "password1"},
                           headers=through("203.0.113.5"))
    assert "Too many wrong passwords" in blocked.get_data(as_text=True)
    # Someone else, through the same proxy: not held up by the first.
    other = proxied.test_client().post("/login", data={"username": "tester", "password": "password1"},
                                       headers=through("198.51.100.7"))
    assert other.status_code == 302 and other.headers["Location"].endswith("/dashboard")
