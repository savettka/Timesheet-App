"""5.2 Goals and streaks: hours a day or a week, the Timesheet written, or
out by a time -- each with today's progress, the current streak and the
best. Leave and rest days don't break a streak; today can't until it's
over. Set in Settings, shown on Home."""
import datetime as dt

import pytest

from app import db, logic
from app.models import Goal, TimeEntry, WorkBlock

from conftest import a_user

T = dt.time
D = dt.date
TODAY = D(2026, 11, 12)  # a Thursday
NOW = dt.datetime(2026, 11, 12, 14, 0)


class FakeEntry:
    def __init__(self, day, login=None, logout=None, override=None):
        self.date, self.login_time, self.logout_time = day, login, logout
        self.target_override, self.breaks = override, []


def day(kind, value, entry, d=None, written=0, now=NOW):
    d = d or (entry.date if entry else TODAY)
    return logic.goal_day(kind, value, a_user(), d, entry, written, TODAY, now)


class TestStreaks:
    def test_runs(self):
        assert logic.streaks(["met", "met", "skip", "met", "missed", "met", "open"]) == (1, 3)
        assert logic.streaks([]) == (0, 0)
        assert logic.streaks(["missed"]) == (0, 0)


class TestDays:
    WED = D(2026, 11, 11)

    def test_hours_a_day(self):
        assert day("daily_hours", 8, FakeEntry(self.WED, T(9, 0), T(17, 0))) == "met"
        assert day("daily_hours", 8, FakeEntry(self.WED, T(9, 0), T(15, 0))) == "missed"
        assert day("daily_hours", 8, None, d=self.WED) == "missed"
        # Today isn't over: not met yet is still open.
        assert day("daily_hours", 8, FakeEntry(TODAY, T(9, 0))) == "open"
        assert day("daily_hours", 4, FakeEntry(TODAY, T(9, 0))) == "met"
        # Sunday, and leave: neither way.
        assert day("daily_hours", 8, None, d=D(2026, 11, 8)) == "skip"
        assert day("daily_hours", 8, FakeEntry(self.WED, override=0)) == "skip"
        assert day("daily_hours", 8, FakeEntry(self.WED, override=4)) == "skip"

    def test_out_by(self):
        seven = 19 * 60
        assert day("logout_by", seven, FakeEntry(self.WED, T(9, 0), T(18, 30))) == "met"
        assert day("logout_by", seven, FakeEntry(self.WED, T(9, 0), T(19, 30))) == "missed"
        # After midnight is later, not earlier.
        assert day("logout_by", seven, FakeEntry(self.WED, T(15, 0), T(0, 30))) == "missed"
        assert day("logout_by", 25 * 60, FakeEntry(self.WED, T(15, 0), T(0, 30))) == "met"
        assert day("logout_by", seven, FakeEntry(TODAY, T(9, 0))) == "open"
        assert day("logout_by", 13 * 60, FakeEntry(TODAY, T(9, 0))) == "missed"  # still in at 2 PM
        assert day("logout_by", seven, None, d=self.WED) == "skip"

    def test_timesheet_written(self):
        worked = FakeEntry(self.WED, T(9, 0), T(17, 0))
        assert day("timesheet_written", 100, worked, written=480) == "met"
        assert day("timesheet_written", 100, worked, written=400) == "missed"
        assert day("timesheet_written", 80, worked, written=400) == "met"
        assert day("timesheet_written", 100, None, d=self.WED) == "skip"
        assert day("timesheet_written", 100, FakeEntry(TODAY, T(9, 0)), written=60) == "open"


def test_a_week_with_leave_asks_less():
    user = a_user()
    days = logic.week_dates(D(2026, 11, 2))
    entries = {d: FakeEntry(d, T(9, 0), T(17, 0)) for d in days[:5]}
    entries[days[5]] = FakeEntry(days[5], override=0)  # Saturday off
    assert logic.goal_week(48, user, days, entries, TODAY, NOW) == ("met", 40.0, 40.0)
    assert logic.goal_week(50, user, days, entries, TODAY, NOW)[0] == "missed"
    leave = {d: FakeEntry(d, override=0) for d in days[:6]}
    assert logic.goal_week(48, user, days, leave, TODAY, NOW)[0] == "skip"


def test_a_streak_runs_over_a_sunday_and_leave():
    user = a_user()
    entries = {}
    d = D(2026, 11, 2)
    while d < TODAY:
        if d.weekday() < 6:
            entries[d] = FakeEntry(d, T(9, 0), T(17, 30))
        d += dt.timedelta(days=1)
    entries[D(2026, 11, 4)] = FakeEntry(D(2026, 11, 4), override=0)  # Wednesday's leave
    entries[TODAY] = FakeEntry(TODAY, T(9, 0))
    first = logic.heatmap_first(TODAY)
    g = logic.goal_progress("daily_hours", 8, user, entries, {}, first, TODAY, NOW)
    # Mon, Tue, Thu, Fri, Sat, then Mon to Wed: eight days; today still going.
    assert (g["current"], g["best"], g["state"]) == (8, 8, "open")
    assert g["label"] == "5h of 8h today" and g["pct"] == 62
    assert g["title"] == "8h a day"
    entries[D(2026, 11, 10)] = FakeEntry(D(2026, 11, 10), T(9, 0), T(12, 0))  # a short Tuesday
    g = logic.goal_progress("daily_hours", 8, user, entries, {}, first, TODAY, NOW)
    assert (g["current"], g["best"]) == (1, 6)  # Mon 2 to Mon 9 Nov, then Wednesday


def test_week_streak():
    user = a_user()
    entries = {}
    d = D(2026, 10, 19)
    while d <= TODAY:
        if d.weekday() < 6:
            entries[d] = FakeEntry(d, T(9, 0), T(17, 0) if d != TODAY else None)
        d += dt.timedelta(days=1)
    g = logic.goal_progress("weekly_hours", 48, user, entries, {}, logic.heatmap_first(TODAY), TODAY, NOW)
    assert (g["current"], g["best"], g["state"], g["unit"]) == (3, 3, "open", "week")
    assert g["label"] == "29h of 48h this week"


def test_out_by_today():
    user = a_user()
    first = logic.heatmap_first(TODAY)
    g = logic.goal_progress("logout_by", 19 * 60, user, {TODAY: FakeEntry(TODAY, T(9, 0))}, {}, first, TODAY, NOW)
    assert g["title"] == "Out by 7:00 PM" and g["label"] == "5h to go" and g["pct"] == 50


# ------------------------------------------------------------- routes

def save_goals(client, on, **values):
    data = {"form_type": "goals", "on": on}
    data.update(values)
    return client.post("/settings", data=data, follow_redirects=True)


def goals(app, user_id):
    with app.app_context():
        return {g.kind: (g.value, g.active) for g in Goal.query.filter_by(user_id=user_id)}


def test_set_in_settings(app, client, user_id):
    r = save_goals(client, ["daily_hours", "logout_by"], daily_hours="8.5", logout_by="19:00",
                   weekly_hours="48", timesheet_written="100")
    assert "Goals saved." in r.get_data(as_text=True)
    assert goals(app, user_id) == {"daily_hours": (8.5, True), "logout_by": (1140.0, True)}
    # Off keeps its value; half past midnight is the morning after.
    save_goals(client, ["logout_by"], daily_hours="9", logout_by="00:30")
    assert goals(app, user_id) == {"daily_hours": (9.0, False), "logout_by": (1470.0, True)}
    page = client.get("/settings").get_data(as_text=True)
    assert 'name="logout_by" value="00:30"' in page and 'name="daily_hours" value="9"' in page


def test_a_bad_value(app, client, user_id):
    r = save_goals(client, ["daily_hours", "timesheet_written"], daily_hours="abc", timesheet_written="150")
    assert "Check the value for Hours a day and Timesheet written." in r.get_data(as_text=True)
    assert goals(app, user_id) == {}


def test_on_home(app, client, user_id):
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Set a goal" in page and 'class="goal ' not in page
    with app.app_context():
        for d in (D(2026, 11, 10), D(2026, 11, 11)):
            db.session.add(TimeEntry(user_id=user_id, date=d, login_time=T(9, 0), logout_time=T(18, 0)))
            db.session.add(WorkBlock(user_id=user_id, date=d, start_time=T(9, 0), end_time=T(18, 0), text="Audit"))
        db.session.add(TimeEntry(user_id=user_id, date=TODAY, login_time=T(9, 0)))
        db.session.commit()
    save_goals(client, ["daily_hours", "timesheet_written", "logout_by"], daily_hours="8",
               timesheet_written="100", logout_by="18:30")
    page = client.get("/dashboard").get_data(as_text=True)
    assert "8h a day" in page and "Timesheet written" in page and "Out by 6:30 PM" in page
    assert "<b>2</b> days in a row · best 2" in page
    assert "4h 30m to go" in page
