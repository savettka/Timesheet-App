"""Every page opens for a signed-in user, and the four punches write what
they should."""
import datetime as dt

import pytest

from app import db, logic
from app.models import BreakSegment, Salary, TimeEntry, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)

PAGES = [
    "/dashboard", "/timesheet", "/timesheet/2026-11-11", "/timesheet/week", "/timesheet/all",
    "/timesheet/all?q=VAT", "/calendar", "/calendar/2026/10", "/calendar/week", "/calendar/day", "/boards",
    "/boards?view=week", "/reports", "/reports?range=month", "/reports/lines.csv", "/history",
    "/history/2026/10", "/entry/2026-11-11", "/pay", "/settings", "/api/status",
]


@pytest.fixture
def some_days(app, user_id):
    """Last week's hours, a line written, and a salary, so pages have
    something to show."""
    with app.app_context():
        for n in (2, 3, 4, 5, 6, 9, 10, 11):
            d = dt.date(2026, 11, n)
            entry = TimeEntry(user_id=user_id, date=d, login_time=T(9, 0), logout_time=T(18, 0))
            entry.breaks.append(BreakSegment(break_start=T(13, 0), break_end=T(13, 45)))
            db.session.add(entry)
        db.session.add(WorkBlock(user_id=user_id, date=dt.date(2026, 11, 11), start_time=T(9, 0),
                                 end_time=T(10, 0), text="VAT return"))
        db.session.add(Salary(user_id=user_id, starts=dt.date(2026, 10, 1), amount=23000))
        db.session.commit()


@pytest.mark.parametrize("path", PAGES)
def test_every_page_opens(client, some_days, path):
    assert client.get(path).status_code == 200


def test_home_from_the_root(client):
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")


def test_signed_out_goes_to_sign_in(app):
    response = app.test_client().get("/dashboard")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_login_break_back_logout(app, client, clock, user_id):
    clock.set(2026, 11, 12, 9, 0)
    assert client.post("/punch/in").status_code == 302
    clock.set(2026, 11, 12, 12, 0)
    client.post("/punch/break/start")
    clock.set(2026, 11, 12, 12, 45)
    client.post("/punch/break/end")
    clock.set(2026, 11, 12, 18, 0)
    client.post("/punch/out")
    with app.app_context():
        entries = TimeEntry.query.filter_by(user_id=user_id).all()
        assert len(entries) == 1
        entry = entries[0]
        assert (entry.date, entry.login_time, entry.logout_time) == (THU, T(9, 0), T(18, 0))
        assert [(b.break_start, b.break_end) for b in entry.breaks] == [(T(12, 0), T(12, 45))]
        assert logic.entry_total_hours(entry) == pytest.approx(8.25)


def test_a_second_login_is_refused_while_logged_in(app, client, clock, user_id):
    clock.set(2026, 11, 12, 9, 0)
    client.post("/punch/in")
    clock.set(2026, 11, 12, 9, 5)
    page = client.post("/punch/in", follow_redirects=True).get_data(as_text=True)
    assert "already logged in" in page
    with app.app_context():
        assert TimeEntry.query.filter_by(user_id=user_id).count() == 1
