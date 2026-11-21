"""Standard hours for each day of the week, from a date on (Settings). Days
before the first set of hours keep the old rule: the daily target on the
ticked workdays, and the weekly target as set."""
import datetime as dt

import pytest

from app import db, logic
from app.models import HoursPlan, TimeEntry, User

D = dt.date
T = dt.time
SHORT_SATURDAY = "8,8,8,8,8,4,0"


@pytest.fixture
def plan(app, user_id):
    """Saturday down to 4h from Mon 16 Nov 2026."""
    with app.app_context():
        db.session.add(HoursPlan(user_id=user_id, starts=D(2026, 11, 16), hours=SHORT_SATURDAY))
        db.session.commit()


def me(user_id):
    return db.session.get(User, user_id)


def test_without_hours_set_nothing_changes(app, user_id):
    with app.app_context():
        user = me(user_id)
        assert user.target_hours_on(D(2026, 11, 21)) == 8 and user.target_hours_on(D(2026, 11, 22)) == 0
        assert logic.week_target_hours(user, {}, D(2026, 11, 16), 2026, 11) == 48


def test_hours_apply_from_their_date(app, user_id, plan):
    with app.app_context():
        user = me(user_id)
        assert user.target_hours_on(D(2026, 11, 14)) == 8  # the Saturday before keeps 8h
        assert user.target_hours_on(D(2026, 11, 21)) == 4
        assert user.target_hours_on(D(2026, 11, 22)) == 0
        assert logic.week_target_hours(user, {}, D(2026, 11, 16), 2026, 11) == 44
        assert logic.week_target_hours(user, {}, D(2026, 11, 9), 2026, 11) == 48


def test_a_short_saturday_is_not_owed_once_it_is_the_standard(app, user_id, plan):
    with app.app_context():
        user = me(user_id)
        entries = {}
        for day in (D(2026, 11, 14), D(2026, 11, 21)):
            entry = TimeEntry(user_id=user_id, date=day, login_time=T(9, 0), logout_time=T(13, 0))
            db.session.add(entry)
            entries[day] = entry
        db.session.commit()
        rows = {r["date"]: r for r in logic.month_summary(user, entries, 2026, 11, D(2026, 11, 25))["rows"]}
        assert rows[D(2026, 11, 14)]["balance_hours"] == -4  # before the new hours: 4h of 8h
        assert rows[D(2026, 11, 21)]["balance_hours"] == 0   # after: 4h of 4h


def test_pay_rate_uses_the_month_s_real_hours(app, user_id, plan):
    with app.app_context():
        user = me(user_id)
        before = logic.month_pay(user, 23000, 2026, 10, 0)
        assert before["workdays"] == 27 and round(before["rate"], 2) == round(23000 / (27 * 8), 2)
        # November: 16 days before the change at the old rule, the rest at the
        # new hours (Saturdays 21 and 28 at 4h).
        november = sum(user.target_hours_on(D(2026, 11, n)) for n in range(1, 31))
        assert november == 200 - 8
        assert logic.month_pay(user, 23000, 2026, 11, 0)["rate"] == pytest.approx(23000 / 192)


def test_settings_saves_replaces_and_removes_hours(app, client, user_id):
    hours = {f"hours_{i}": v for i, v in enumerate(["8", "8", "8", "8", "8", "4", "0"])}
    client.post("/settings", data={"form_type": "hours", "starts": "2026-11-16", **hours})
    client.post("/settings", data={"form_type": "hours", "starts": "2026-11-16", **hours, "hours_5": "5"})
    with app.app_context():
        plans = HoursPlan.query.filter_by(user_id=user_id).all()
        assert [(p.starts, p.hours) for p in plans] == [(D(2026, 11, 16), "8,8,8,8,8,5,0")]
        plan_id = plans[0].id
    client.post("/settings", data={"form_type": "hours_remove", "plan_id": plan_id})
    with app.app_context():
        assert HoursPlan.query.filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize("bad", ["-1", "25", "nan", "x"])
def test_settings_refuses_odd_hours(app, client, user_id, bad):
    hours = {f"hours_{i}": "8" for i in range(7)}
    client.post("/settings", data={"form_type": "hours", "starts": "2026-11-16", **hours, "hours_2": bad})
    with app.app_context():
        assert HoursPlan.query.filter_by(user_id=user_id).count() == 0


def test_the_breaks_form_leaves_the_hours_alone(app, client, user_id):
    client.post("/settings", data={"form_type": "targets", "weekday_break_minutes": "45",
                                   "saturday_break_minutes": "30"})
    with app.app_context():
        user = me(user_id)
        assert user.weekday_break_minutes == 45
        assert (user.daily_target_hours, user.weekly_target_hours, user.workdays) == (8, 48, "0,1,2,3,4,5")


def test_settings_shows_seven_boxes_and_the_week(client, plan):
    page = client.get("/settings").get_data(as_text=True)
    assert all(f'name="hours_{i}"' in page for i in range(7))
    assert "44h a week" in page and "From Mon 16 Nov 2026" in page
