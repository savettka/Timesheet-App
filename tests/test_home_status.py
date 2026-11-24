"""Home's status card says where the day is as the top bar does: the fact
with its time ("In at 9:00 AM"), no coloured dot, and one line under it."""

import datetime as dt
import re

from app import db
from app.models import TimeEntry

THU = dt.date(2026, 11, 12)


def card(client):
    page = client.get("/dashboard").get_data(as_text=True)
    html = re.search(r'<section class="card status-card.*?</section>', page, re.S).group(0)
    title = re.search(r'class="status-title">\s*(.*?)\s*</h2>', html, re.S).group(1)
    sub = re.search(r'class="status-sub">\s*(.*?)\s*</p>', html, re.S)
    words = re.sub(r"<[^>]+>", "", sub.group(1)).strip() if sub else None
    return html, title, words


def log_in(app, user_id, clock):
    clock.set(2026, 11, 12, 9, 0)
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=dt.time(9, 0)))
        db.session.commit()


def test_not_in_yet(client, clock):
    clock.set(2026, 11, 12, 8, 0)
    html, title, sub = card(client)
    assert title == "Not in yet" and sub == "8h due today"
    assert "status-dot" not in html


def test_in_on_a_break_and_out(app, client, user_id, clock):
    log_in(app, user_id, clock)
    clock.set(2026, 11, 12, 10, 0)
    assert card(client)[1:] == ("In at 9:00 AM", None)

    clock.set(2026, 11, 12, 13, 0)
    client.post("/punch/break/start")
    clock.set(2026, 11, 12, 13, 20)
    assert card(client)[1:] == ("Break at 1:00 PM", "20m so far")

    client.post("/punch/break/end")
    clock.set(2026, 11, 12, 18, 30)
    client.post("/punch/out")
    assert card(client)[1:] == ("Out at 6:30 PM", "In at 9:00 AM")


def test_a_forgotten_logout(app, client, user_id, clock):
    log_in(app, user_id, clock)
    clock.set(2026, 11, 14, 10, 0)  # Thursday's shift still open on Saturday
    html, title, sub = card(client)
    assert title == "No logout on Thu 12 Nov" and sub == "In at 9:00 AM"
    assert "didn't log out" not in html
