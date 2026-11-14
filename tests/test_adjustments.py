"""7.2 Balance adjustments: hours added to or taken off a month's balance by
hand, from History -- counted through logic.adjusted_month_summary, which
wraps month_summary without changing it."""
import datetime as dt
from types import SimpleNamespace

from app import db, logic
from app.main import parse_hours
from app.models import BalanceAdjustment, Salary, TimeEntry, User

from conftest import a_user

T = dt.time
D = dt.date
TODAY = D(2026, 11, 12)


def test_parse_hours():
    assert parse_hours("2") == 2 and parse_hours("-1.5") == -1.5
    assert parse_hours("+2:30") == 2.5 and parse_hours("−0:45") == -0.75 and parse_hours(":30") == 0.5
    for bad in ("", "0", "abc", "1:75", "101", "2:3x", "1e400", "nan"):
        assert parse_hours(bad) is None, bad


def test_the_wrapper_leaves_month_summary_alone():
    user = a_user()
    entries = {D(2026, 11, 2): SimpleNamespace(date=D(2026, 11, 2), login_time=T(9, 0), logout_time=T(19, 0),
                                               target_override=None, breaks=[])}
    now = dt.datetime(2026, 11, 12, 14, 0)
    plain = logic.month_summary(user, entries, 2026, 11, TODAY, now=now)
    adj = [SimpleNamespace(id=1, date=D(2026, 11, 5), hours=2.5, reason="TOIL"),
           SimpleNamespace(id=2, date=D(2026, 10, 30), hours=9, reason="another month")]
    out = logic.adjusted_month_summary(user, entries, 2026, 11, TODAY, adj, now=now)
    assert out["rows"] == plain["rows"] and out["worked_hours"] == plain["worked_hours"]
    assert out["balance_hours"] == plain["balance_hours"] + 2.5
    assert out["unadjusted_balance_hours"] == plain["balance_hours"]
    assert [a.reason for a in out["adjustments"]] == ["TOIL"] and out["adjusted_hours"] == 2.5
    assert logic.adjusted_month_summary(user, entries, 2026, 11, TODAY, [], now=now)["balance_hours"] == \
        plain["balance_hours"]


def test_added_from_history(app, client, user_id):
    with app.app_context():
        db.session.add(Salary(user_id=user_id, starts=D(2026, 1, 1), amount=23000))
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 11, 2), login_time=T(9, 0), logout_time=T(17, 0)))
        db.session.commit()
    before = client.get("/history/2026/11").get_data(as_text=True)
    r = client.post("/history/adjust", data={"date": "2026-11-05", "hours": "+2:30", "reason": "  Time off   in lieu "},
                    follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Adjusted the balance +2h 30m on Thu 05 Nov." in page
    assert "Includes +2h 30m of adjustments" in page and "Time off in lieu" in page
    assert 'class="adj-row"' in page and "adj-row" not in before
    with app.app_context():
        a = BalanceAdjustment.query.one()
        assert (a.date, a.hours, a.reason) == (D(2026, 11, 5), 2.5, "Time off in lieu")
        a_id = a.id
    # Home and Pay count it too.
    assert "Includes" not in client.get("/dashboard").get_data(as_text=True)  # only History says so
    r = client.post(f"/history/adjust/{a_id}/delete", follow_redirects=True)
    assert "Removed the +2h 30m adjustment on Thu 05 Nov." in r.get_data(as_text=True)
    with app.app_context():
        assert BalanceAdjustment.query.count() == 0


def test_home_and_pay_balances_move(app, client, user_id):
    with app.app_context():
        db.session.add(Salary(user_id=user_id, starts=D(2026, 1, 1), amount=23000))
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 11, 2), login_time=T(9, 0), logout_time=T(17, 0)))
        db.session.commit()
    home = client.get("/dashboard").get_data(as_text=True)
    client.post("/history/adjust", data={"date": "2026-11-05", "hours": "100"})
    after = client.get("/dashboard").get_data(as_text=True)
    assert home != after
    pay = client.get("/pay").get_data(as_text=True)
    assert "₹" in pay


def test_refused(app, client, user_id):
    r = client.post("/history/adjust", data={"date": "2026-11-05", "hours": "lots"}, follow_redirects=True)
    assert "Give the adjustment a date and its hours" in r.get_data(as_text=True)
    with app.app_context():
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        theirs = BalanceAdjustment(user_id=other.id, date=D(2026, 11, 5), hours=1, reason="")
        db.session.add(theirs)
        db.session.commit()
        theirs_id = theirs.id
    assert client.post(f"/history/adjust/{theirs_id}/delete").status_code == 404
