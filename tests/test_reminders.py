"""6.1 Reminders in STM while it's open: no login by a set time, still
logged in after so many hours, a break over the allowance, and the last
day's lines not written -- a bar on any page from /api/reminders, set in
Settings."""
import datetime as dt
import json

import pytest

from app import db, reminders
from app.models import BreakSegment, TimeEntry, User, WorkBlock

T = dt.time
D = dt.date
THU = D(2026, 11, 12)  # the clock fixture's day, 2 PM
WED = D(2026, 11, 11)


def keys(client):
    return [r["key"] for r in client.get("/api/reminders").get_json()["reminders"]]


def texts(client):
    return [r["text"] for r in client.get("/api/reminders").get_json()["reminders"]]


def test_settings_over_defaults(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        assert reminders.settings_for(user) == reminders.DEFAULTS
        user.reminder_settings = json.dumps({"no_login": {"on": False, "at": 9999}, "long_day": {"hours": "x"},
                                             "idle": {"minutes": 30, "on": True}, "nonsense": 1})
        on = reminders.settings_for(user)
        assert on["no_login"] == {"on": False, "at": 600}
        assert on["long_day"] == {"on": True, "hours": 10.0}
        assert on["idle"] == {"on": True, "minutes": 30}
        for bad in ("{not json", "[1]"):
            user.reminder_settings = bad
            assert reminders.settings_for(user) == reminders.DEFAULTS


def test_no_login_yet(app, client, user_id, clock):
    assert keys(client) == ["no_login:2026-11-12"]
    clock.set(2026, 11, 12, 9, 30)
    assert keys(client) == []          # before 10 AM
    clock.set(2026, 11, 15, 11, 0)
    assert keys(client) == []          # Sunday
    clock.set(2026, 11, 12, 14, 0)
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=THU, target_override=0.0, leave_type="full"))
        db.session.commit()
    assert keys(client) == []          # leave


def test_long_day_and_long_break(app, client, user_id, clock):
    with app.app_context():
        entry = TimeEntry(user_id=user_id, date=THU, login_time=T(3, 0))
        db.session.add(entry)
        db.session.flush()
        db.session.add(BreakSegment(entry_id=entry.id, break_start=T(12, 30)))
        db.session.commit()
    # 3 AM to 12:30, then on a break since: 9h 30m worked, 1h 30m of break.
    assert texts(client) == ["On a break: 1h 30m today, over your 1h."]
    clock.set(2026, 11, 12, 13, 0)
    assert texts(client) == []
    with app.app_context():
        BreakSegment.query.one().break_end = T(13, 0)
        db.session.commit()
    clock.set(2026, 11, 12, 14, 0)
    assert texts(client) == ["Still logged in after 10h 30m worked."]  # 11h less the break


def test_last_days_lines(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 0), logout_time=T(12, 0)))
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0)))
        db.session.add(WorkBlock(user_id=user_id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="Post"))
        db.session.commit()
    data = client.get("/api/reminders").get_json()["reminders"]
    assert data == [{"key": "unwritten:2026-11-11", "text": "Yesterday's timesheet has 2 lines to fill in.",
                     "href": "/timesheet/2026-11-11"}]


def test_set_in_settings(app, client, user_id):
    r = client.post("/settings", data={"form_type": "reminders", "no_login_on": "1", "no_login_at": "15:30",
                                       "long_day_on": "1", "long_day_hours": "9.5", "unwritten_on": "1"},
                    follow_redirects=True)
    assert "Reminders saved." in r.get_data(as_text=True)
    with app.app_context():
        on = reminders.settings_for(db.session.get(User, user_id))
    assert on["no_login"] == {"on": True, "at": 930} and on["long_day"] == {"on": True, "hours": 9.5}
    assert not on["long_break"]["on"]
    assert keys(client) == []  # 2 PM is before 3:30 PM now
    page = client.get("/settings").get_data(as_text=True)
    assert 'name="no_login_at" value="15:30"' in page and "In STM while it's open" in page


def test_the_bar_is_on_every_page(client):
    for page in ("/dashboard", "/timesheet", "/reports", "/settings"):
        html = client.get(page).get_data(as_text=True)
        assert 'id="rm-bar"' in html and 'data-reminders-url="/api/reminders"' in html
