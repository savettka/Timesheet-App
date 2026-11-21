"""Overtime by the week: Monday to Friday bank their hours, Saturday owes
whatever is left of the week's 48h, and the week settles the moment its
Saturday is logged into -- so logging out changes nothing."""
import datetime as dt

import pytest

from app import db, logic
from app.models import TimeEntry, User

D = dt.date
T = dt.time

# The user's real week, Mon 16 to Fri 20 Nov 2026: 45h 53m.
WEEK = {
    16: (T(12, 46), T(23, 27), 60),
    17: (T(12, 53), T(22, 32), 60),
    18: (T(12, 46), T(22, 26), 60),
    19: (T(12, 36), T(23, 31), 62),
    20: (T(12, 28), T(22, 28), 60),
}


def add_day(user_id, day, login, logout=None, break_minutes=0):
    from app.models import BreakSegment
    entry = TimeEntry(user_id=user_id, date=D(2026, 11, day), login_time=login, logout_time=logout)
    if break_minutes:
        start = dt.datetime.combine(D(2026, 11, day), T(17, 0))
        entry.breaks.append(BreakSegment(break_start=start.time(),
                                         break_end=(start + dt.timedelta(minutes=break_minutes)).time()))
    db.session.add(entry)
    return entry


def november(app, user_id, today, now):
    with app.app_context():
        user = db.session.get(User, user_id)
        entries = {e.date: e for e in TimeEntry.query.filter_by(user_id=user_id)}
        return logic.month_summary(user, entries, 2026, 11, today, now=now)


@pytest.fixture
def mon_to_fri(app, user_id):
    with app.app_context():
        for day, (login, logout, brk) in WEEK.items():
            add_day(user_id, day, login, logout, brk)
        db.session.commit()


def week_minutes(summary):
    return round(sum(r["balance_hours"] for r in summary["rows"] if D(2026, 11, 16) <= r["date"] <= D(2026, 11, 22)) * 60)


def test_weekdays_bank_and_saturday_owes_what_is_left(app, user_id, mon_to_fri):
    summary = november(app, user_id, D(2026, 11, 21), dt.datetime(2026, 11, 21, 11, 0))
    rows = {r["date"].day: r for r in summary["rows"]}
    assert all(rows[d]["balance_hours"] == 0 for d in range(16, 21))  # banked, not overtime yet
    assert round(rows[16]["banked_hours"] * 60) == 101                 # +1h 41m against its 8h
    assert rows[21]["settles"] and not rows[21]["settled"]             # Saturday not started
    assert round(rows[21]["target_left"] * 60) == 127                  # 48h - 45h 53m = 2h 07m
    assert week_minutes(summary) == 0


def test_saturday_settles_at_login_and_logout_changes_nothing(app, clock, user_id, mon_to_fri):
    with app.app_context():
        add_day(user_id, 21, T(11, 55))
        db.session.commit()
    at_login = november(app, user_id, D(2026, 11, 21), dt.datetime(2026, 11, 21, 11, 55))
    assert week_minutes(at_login) == -127                              # 2h 07m to go
    clock.set(2026, 11, 21, 16, 37)
    live = november(app, user_id, D(2026, 11, 21), dt.datetime(2026, 11, 21, 16, 37))
    assert week_minutes(live) == 155                                   # 4h 42m worked: +2h 35m
    with app.app_context():
        entry = TimeEntry.query.filter_by(user_id=user_id, date=D(2026, 11, 21)).one()
        entry.logout_time = T(16, 37)
        db.session.commit()
    after = november(app, user_id, D(2026, 11, 21), dt.datetime(2026, 11, 21, 16, 38))
    assert week_minutes(after) == 155                                  # unchanged at logout


def test_a_week_past_its_target_before_saturday_keeps_it(app, user_id):
    with app.app_context():
        for day in range(16, 21):
            add_day(user_id, day, T(8, 0), T(18, 0))                  # 10h a day: 50h by Friday
        add_day(user_id, 21, T(9, 0), T(10, 0))                        # and 1h on Saturday
        db.session.commit()
    summary = november(app, user_id, D(2026, 11, 22), dt.datetime(2026, 11, 22, 12, 0))
    assert week_minutes(summary) == (51 - 48) * 60


def test_a_missed_saturday_still_settles_once_it_has_passed(app, user_id, mon_to_fri):
    summary = november(app, user_id, D(2026, 11, 23), dt.datetime(2026, 11, 23, 9, 0))
    assert week_minutes(summary) == round((45 + 53 / 60 - 48) * 60)


def test_a_week_across_a_month_end_settles_each_part(app, user_id):
    # Mon 30 Nov is November's part of its week: its own 8h, settled that day.
    with app.app_context():
        add_day(user_id, 30, T(9, 0), T(19, 0))
        db.session.commit()
    summary = november(app, user_id, D(2026, 12, 1), dt.datetime(2026, 12, 1, 9, 0))
    row = summary["rows"][-1]
    assert row["settles"] and round(row["balance_hours"] * 60) == 120


def test_leave_moves_the_week_target_and_rest_day_work_is_overtime(app, user_id):
    with app.app_context():
        leave = TimeEntry(user_id=user_id, date=D(2026, 11, 16), target_override=0.0, leave_type="full",
                          leave_label="Holiday")
        db.session.add(leave)
        for day in range(17, 22):
            add_day(user_id, day, T(9, 0), T(17, 0))                  # 8h Tue to Sat: 40h of 40h
        add_day(user_id, 22, T(10, 0), T(12, 0))                       # 2h on Sunday, the rest day
        db.session.commit()
    summary = november(app, user_id, D(2026, 11, 23), dt.datetime(2026, 11, 23, 9, 0))
    assert week_minutes(summary) == 120
