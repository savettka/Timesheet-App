"""1.2 The command palette: on every signed-in page, and /api/palette with
the day's next punches, the timer and the recent tasks."""
import datetime as dt

import pytest

from app import db
from app.models import BreakSegment, TimeEntry, User, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)


def palette(client):
    return client.get("/api/palette").get_json()


def test_the_dialog_and_its_button_on_every_page(client):
    for path in ("/dashboard", "/reports", "/settings"):
        page = client.get(path).get_data(as_text=True)
        assert '<dialog class="cp" id="palette"' in page
        assert 'role="combobox"' in page and 'role="listbox"' in page and 'aria-live="polite"' in page
        assert 'aria-keyshortcuts="Control+K"' in page


def test_signed_out_has_none(app):
    assert app.test_client().get("/api/palette").status_code == 302
    assert 'id="palette"' not in app.test_client().get("/login").get_data(as_text=True)


def test_not_logged_in_offers_login(client):
    got = palette(client)
    assert got["state"] == "out" and got["word"] == "Not logged in"
    assert got["punches"] == [{"label": "Login", "url": "/punch/in"}]
    assert got["can_time"] is False and got["timer"] is None
    assert got["today"] == "2026-11-12"


def test_working_offers_break_and_logout(app, client, clock, user_id):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/punch/in")
    got = palette(client)
    assert got["state"] == "working"
    assert [p["label"] for p in got["punches"]] == ["Break", "Logout"]
    assert got["can_time"] is True


def test_on_a_break_offers_back(client, clock):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/punch/in")
    clock.set(2026, 11, 12, 12, 0)
    client.post("/punch/break/start")
    got = palette(client)
    assert got["state"] == "break"
    assert got["punches"] == [{"label": "Back from break", "url": "/punch/break/end"}]


def test_logged_out_offers_login_again(client, clock):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/punch/in")
    clock.set(2026, 11, 12, 18, 0)
    client.post("/punch/out")
    assert palette(client)["punches"] == [{"label": "Login again", "url": "/punch/in"}]


def test_a_forgotten_logout_offers_the_fix(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=dt.date(2026, 11, 9), login_time=T(9, 0)))
        db.session.commit()
    got = palette(client)
    assert got["state"] == "stale" and got["punches"] == []
    assert got["fix_url"] == "/entry/2026-11-09"


def test_the_timer_and_the_last_ten_tasks(app, client, clock, user_id):
    with app.app_context():
        for n in range(12):
            db.session.add(WorkBlock(user_id=user_id, date=dt.date(2026, 11, 1 + n % 11), start_time=T(9, 0),
                                     end_time=T(10, 0), text=f"Task {n}"))
        db.session.add(WorkBlock(user_id=user_id, date=THU, start_time=T(8, 0), end_time=T(9, 0), text="Break"))
        db.session.commit()
    clock.set(2026, 11, 12, 10, 0)
    client.post("/punch/in")
    client.post("/timer/start", data={"text": "Emails"})
    clock.set(2026, 11, 12, 10, 5)
    got = palette(client)
    assert got["timer"] == {"text": "Emails", "clock": "0:05:00"}
    assert len(got["recent"]) == 10 and "Break" not in got["recent"]
    assert (got["start_url"], got["stop_url"]) == ("/timer/start", "/timer/stop")


def test_only_your_own_tasks(app, client, user_id):
    with app.app_context():
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        db.session.add(WorkBlock(user_id=other.id, date=THU, start_time=T(9, 0), end_time=T(10, 0), text="Not mine"))
        db.session.commit()
    assert "Not mine" not in palette(client)["recent"]
