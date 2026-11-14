"""7.4 The quick link: /q shows the next step for whoever is signed in, one
tap away -- a visit never punches -- and the app's shortcuts ask for one."""
import datetime as dt
import json
import re

from app import db
from app.models import BreakSegment, TimeEntry

T = dt.time
THU = dt.date(2026, 11, 12)  # the clock fixture's day, 2 PM


def actions(client, url="/q"):
    page = client.get(url).get_data(as_text=True)
    return re.findall(r'<form method="post" action="(/punch/[^"]+)"', page), page


def test_signed_out_goes_to_sign_in_and_back(app, user_id):
    c = app.test_client()
    r = c.get("/q")
    assert r.status_code == 302 and "next=%2Fq" in r.headers["Location"]
    r = c.post("/login?next=/q", data={"username": "tester", "password": "password1"})
    assert r.headers["Location"].endswith("/q")


def test_each_state(app, client, user_id):
    found, page = actions(client)
    assert found == ["/punch/in"] and "Not logged in" in page
    with app.app_context():
        assert TimeEntry.query.count() == 0  # looking never punches
        entry = TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0))
        db.session.add(entry)
        db.session.commit()
    assert actions(client)[0] == ["/punch/break/start", "/punch/out"]
    assert actions(client, "/q?want=logout")[0] == ["/punch/out", "/punch/break/start"]
    with app.app_context():
        db.session.add(BreakSegment(entry_id=TimeEntry.query.one().id, break_start=T(13, 0)))
        db.session.commit()
    found, page = actions(client, "/q?want=login")
    assert found == ["/punch/break/end"] and "On break" in page and "That isn't the next step right now." in page
    with app.app_context():
        e = TimeEntry.query.one()
        e.breaks[0].break_end, e.logout_time = T(13, 30), T(13, 50)
        db.session.commit()
    found, page = actions(client)
    assert found == ["/punch/in"] and "Login again" in page


def test_a_forgotten_logout(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=dt.date(2026, 11, 10), login_time=T(9, 0)))
        db.session.commit()
    found, page = actions(client)
    assert found == [] and "/entry/2026-11-10" in page and "Logout missing" in page


def test_one_tap(app, client, user_id):
    client.post("/punch/in")
    with app.app_context():
        assert TimeEntry.query.one().login_time is not None


def test_shortcuts(app):
    with open(app.static_folder + "/manifest.json", encoding="utf-8") as f:
        shortcuts = json.load(f)["shortcuts"]
    assert [(s["name"], s["url"]) for s in shortcuts] == [
        ("Login", "/q?want=login"), ("Break", "/q?want=break"), ("Logout", "/q?want=logout")]
