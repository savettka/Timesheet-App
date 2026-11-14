"""5.1 The year heatmap: a square a day for the last 12 months on Home and
Reports, shaded by hours against the day's target, leave striped, each
square opening its day -- built from one query by logic.year_heatmap."""
import datetime as dt

import pytest
from sqlalchemy import event

import app.main as main
from app import db, logic
from app.models import BreakSegment, TimeEntry, User

from conftest import a_user

T = dt.time
D = dt.date
TODAY = D(2026, 11, 12)  # a Thursday; the clock fixture's day


class TestLevels:
    def test_shades(self):
        assert logic.heat_level(0, 8) == 0
        assert logic.heat_level(3, 8) == 1
        assert logic.heat_level(5, 8) == 2
        assert logic.heat_level(8, 8) == 3
        assert logic.heat_level(8.5, 8) == 3
        assert logic.heat_level(9, 8) == 4
        assert logic.heat_level(1, 0) == 4  # work on a rest day is all over


class FakeEntry:
    def __init__(self, day, login=None, logout=None, override=None):
        self.date, self.login_time, self.logout_time = day, login, logout
        self.target_override, self.breaks = override, []


def test_the_shape_of_the_year():
    user = a_user()
    entries = {
        D(2026, 11, 9): FakeEntry(D(2026, 11, 9), T(9, 0), T(18, 0)),   # Mon: 9h, over
        D(2026, 11, 10): FakeEntry(D(2026, 11, 10), override=0),         # Tue: leave
        D(2026, 1, 5): FakeEntry(D(2026, 1, 5), T(9, 0), T(13, 0)),      # half the day
    }
    hm = logic.year_heatmap(user, entries, TODAY, now=dt.datetime(2026, 11, 12, 14, 0))
    assert len(hm["columns"]) == 53 and all(len(c) == 7 for c in hm["columns"])
    assert hm["first"] == D(2025, 11, 10) and hm["first"].weekday() == 0
    days = {d["date"]: d for c in hm["columns"] for d in c}
    assert days[D(2026, 11, 9)]["kind"] == "worked" and days[D(2026, 11, 9)]["level"] == 4
    assert days[D(2026, 11, 10)]["kind"] == "leave"
    assert days[D(2026, 11, 11)]["kind"] == "missed"   # a workday with nothing
    assert days[D(2026, 11, 8)]["kind"] == "rest"      # Sunday
    assert days[D(2026, 11, 12)]["kind"] == "none"     # today, nothing yet
    assert days[D(2026, 11, 13)]["kind"] == "future"
    assert days[D(2026, 1, 5)]["level"] == 2
    assert days[D(2026, 1, 6)]["kind"] == "missed"
    # Before the first day recorded: blank, not a year of missed days.
    assert days[D(2026, 1, 4)]["kind"] == "blank"
    assert days[D(2025, 12, 1)]["kind"] == "blank"
    assert hm["worked_hours"] == 13 and hm["days_worked"] == 2
    names = [name for _, name in hm["months"]]
    assert names == ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
    assert hm["months"][1][0] == 3  # 1 December 2025 is in the fourth week


@pytest.fixture
def year(app, user_id):
    with app.app_context():
        for i, day in enumerate((D(2026, 3, 2), D(2026, 6, 15), D(2026, 11, 9), D(2026, 11, 11))):
            entry = TimeEntry(user_id=user_id, date=day, login_time=T(9, 0), logout_time=T(18, 0))
            db.session.add(entry)
            db.session.flush()
            db.session.add(BreakSegment(entry_id=entry.id, break_start=T(13, 0), break_end=T(14, 0)))
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 11, 10), target_override=0.0,
                                 leave_type="full", leave_label="Holiday"))
        db.session.commit()


def test_one_query_for_the_year(app, user_id, year, clock):
    with app.app_context():
        user = db.session.get(User, user_id)
        seen = []
        listen = lambda *a, **k: seen.append(a[2])  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listen)
        try:
            hm = main.heatmap_view(user, TODAY, clock.now)
        finally:
            event.remove(db.engine, "before_cursor_execute", listen)
        assert len(seen) == 1
        assert hm["days_worked"] == 4 and hm["total"] == "32h"


def test_on_home_and_reports(client, year):
    for page in ("/dashboard", "/reports"):
        html = client.get(page).get_data(as_text=True)
        assert 'class="card hm-card"' in html
        assert 'href="/timesheet/2026-11-11"' in html
        assert 'aria-label="Wed 11 Nov 2026: 8h of 8h"' in html
        assert 'aria-label="Tue 10 Nov 2026: leave"' in html and "is-leave" in html
        # Today is the one stop for Tab, and opens plain /timesheet.
        assert 'href="/timesheet"\n           aria-label="Thu 12 Nov 2026: nothing yet" tabindex="0"' in html
        assert html.count('tabindex="0"></a>') >= 1
