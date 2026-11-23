"""1.1 The timer bar: on every signed-in page, Start and Stop come back to
the page they were pressed on, and a timer started on one page is still
running, with the right time, on the next."""
import datetime as dt
import re

import pytest

from app import db
from app.models import TimeEntry, User, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)


@pytest.fixture
def logged_in(app, user_id, clock):
    clock.set(2026, 11, 12, 9, 0)
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0)))
        db.session.commit()


def bar_of(page):
    return page.split('class="tb tb-bar"', 1)[1].split("</header>", 1)[0]


@pytest.mark.parametrize("path", ["/reports", "/calendar", "/settings", "/pay", "/boards", "/history"])
def test_on_every_page(client, logged_in, path):
    bar = bar_of(client.get(path).get_data(as_text=True))
    assert 'data-state="working"' in bar and ">In at 9:00 AM<" in bar
    assert 'name="text"' in bar and "/timer/start" in bar
    assert "tb-dot" not in bar and "Logged in" not in bar


def test_start_on_reports_still_running_on_calendar(client, logged_in, clock, app, user_id):
    clock.set(2026, 11, 12, 10, 0)
    r = client.post("/timer/start", data={"text": "VAT return for a client", "back": "/reports?range=month"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/reports?range=month")
    clock.set(2026, 11, 12, 10, 25, 30)
    page = client.get("/calendar").get_data(as_text=True)
    bar = bar_of(page)
    assert "VAT return for a client" in bar
    assert 'data-elapsed="1530"' in page and "0:25:30" in bar
    page = client.get("/calendar").get_data(as_text=True)  # and after a reload
    assert "0:25:30" in bar_of(page)


def test_stop_comes_back_and_writes_the_line(client, logged_in, clock, app, user_id):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/timer/start", data={"text": "Bank reconciliation", "back": "/boards"})
    clock.set(2026, 11, 12, 11, 15)
    r = client.post("/timer/stop", data={"back": "/boards?view=week"})
    assert r.headers["Location"].endswith("/boards?view=week")
    with app.app_context():
        line = WorkBlock.query.filter_by(user_id=user_id, text="Bank reconciliation").one()
        assert (line.start_time, line.end_time) == (T(10, 0), T(11, 15))
        assert db.session.get(User, user_id).timer_start is None


def test_never_back_to_another_site(client, logged_in):
    for back in ("//evil.example", "https://evil.example/x", "dashboard"):
        r = client.post("/timer/start", data={"text": "x", "back": back})
        assert r.headers["Location"].endswith("/dashboard")
        client.post("/timer/stop")


def test_the_card_still_goes_back_to_the_timesheet(client, logged_in):
    r = client.post("/timer/start", data={"text": "Payroll", "back": "timesheet"})
    assert r.headers["Location"].endswith("/timesheet")


def test_not_logged_in_shows_the_state_and_no_box(client):
    bar = bar_of(client.get("/reports").get_data(as_text=True))
    assert 'data-state="out"' in bar and ">Not in yet<" in bar
    assert 'name="text"' not in bar


def test_on_a_break(client, logged_in, clock, app, user_id):
    clock.set(2026, 11, 12, 13, 0)
    client.post("/punch/break/start")
    bar = bar_of(client.get("/reports").get_data(as_text=True))
    assert 'data-state="break"' in bar and ">Break at 1:00 PM<" in bar and 'name="text"' not in bar


def test_logged_out_says_when(client, logged_in, clock):
    clock.set(2026, 11, 12, 18, 30)
    client.post("/punch/out")
    bar = bar_of(client.get("/reports").get_data(as_text=True))
    assert 'data-state="done"' in bar and ">Out at 6:30 PM<" in bar


def test_a_forgotten_logout_names_the_day(client, logged_in, clock):
    clock.set(2026, 11, 14, 10, 0)  # Thursday's shift still open on Saturday
    bar = bar_of(client.get("/reports").get_data(as_text=True))
    assert 'data-state="stale"' in bar and ">No logout on Thu 12 Nov<" in bar


def test_the_phone_pill_and_sheet(client, logged_in, clock):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/timer/start", data={"text": "Emails"})
    page = client.get("/reports").get_data(as_text=True)
    assert re.search(r'class="tb-pill" data-state="working"[^>]*aria-label="Timer: Emails, In at 9:00 AM"', page)
    sheet = page.split('id="tb-sheet"', 1)[1].split("</dialog>", 1)[0]
    assert "Stop the timer: Emails" in sheet and "Switch to" in sheet


def test_the_phone_pill_is_only_play_or_pause(client, logged_in, clock):
    def pill():
        page = client.get("/reports").get_data(as_text=True)
        return re.search(r'<button type="button" class="tb-pill".*?</button>', page, re.S).group(0)

    clock.set(2026, 11, 12, 10, 0)
    idle = pill()
    assert "tb-dot" not in idle and "timer-clock" not in idle
    assert 'd="M8 5.5v13' in idle  # play
    client.post("/timer/start", data={"text": "Emails"})
    running = pill()
    assert "tb-dot" not in running and "timer-clock" not in running
    assert '<rect x="7" y="6"' in running and 'd="M8 5.5v13' not in running  # pause


def test_status_carries_the_timer(client, logged_in, clock):
    clock.set(2026, 11, 12, 10, 0)
    client.post("/timer/start", data={"text": "Emails"})
    clock.set(2026, 11, 12, 10, 1)
    assert client.get("/api/status").get_json()["timer"] == {"text": "Emails", "elapsed": 60}


def test_signed_out_pages_have_no_bar(app):
    page = app.test_client().get("/login").get_data(as_text=True)
    assert "tb-bar" not in page and "tb-pill" not in page
