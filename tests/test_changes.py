"""7.1 Change history: every change to a day's times, written by one hook
whatever page made it, with where it came from -- and days whose punch
times were typed by hand marked on History, Calendar and the exports."""
import datetime as dt

import pytest

from app import changes, db
from app.models import BreakSegment, EntryChange, TimeEntry, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)  # the clock fixture's day, 2 PM
WED = dt.date(2026, 11, 11)


def log(app, day=None):
    with app.app_context():
        q = EntryChange.query.order_by(EntryChange.id)
        if day:
            q = q.filter_by(entry_date=day)
        return [(c.field, c.old_value, c.new_value, c.source) for c in q]


def test_punches(app, client, user_id, clock):
    clock.set(2026, 11, 12, 9, 0)
    client.post("/punch/in")
    clock.set(2026, 11, 12, 13, 0)
    client.post("/punch/break/start")
    clock.set(2026, 11, 12, 13, 45)
    client.post("/punch/break/end")
    clock.set(2026, 11, 12, 18, 0)
    client.post("/punch/out", data={"time": "17:55"})  # a time typed in: by hand
    assert log(app) == [
        ("Login", "", "09:00", "punch"),
        ("Break start", "", "13:00", "punch"),
        ("Break end", "", "13:45", "punch"),
        ("Logout", "", "17:55", "edit"),
    ]


def test_the_day_editor(app, client, user_id):
    form = {"login_time": "09:00", "logout_time": "18:00", "break_start": ["13:00"], "break_end": ["14:00"]}
    client.post(f"/entry/{WED}", data=form)
    assert log(app) == [("Login", "", "09:00", "edit"), ("Logout", "", "18:00", "edit"),
                        ("Break start", "", "13:00", "edit"), ("Break end", "", "14:00", "edit")]
    # Saved again as it is: nothing new, breaks and all.
    client.post(f"/entry/{WED}", data=form)
    assert len(log(app)) == 4
    # The logout moved, and the break taken out.
    client.post(f"/entry/{WED}", data={"login_time": "09:00", "logout_time": "18:30"})
    assert log(app)[4:] == [("Logout", "18:00", "18:30", "edit"), ("Break removed", "13:00–14:00", "", "edit")]
    client.post(f"/entry/{WED}/delete")
    assert log(app)[-1] == ("Day deleted", "09:00", "", "edit")


def test_seconds_are_not_a_change(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 2, 13), logout_time=T(18, 0, 41)))
        db.session.commit()
    client.post(f"/entry/{WED}", data={"login_time": "09:02", "logout_time": "18:00"})
    assert log(app) == []


def test_line_times_but_not_words(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 0), logout_time=T(18, 0)))
        block = WorkBlock(user_id=user_id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="Audit")
        db.session.add(block)
        db.session.commit()
        block_id = block.id
    r = client.post("/calendar/line", data={"id": block_id, "date": WED.isoformat(), "start": "09:00",
                                            "end": "10:30", "text": "Audit and VAT"})
    assert r.status_code in (200, 302)
    assert log(app) == [("Line end", "10:00", "10:30", "edit")]


def test_the_timer_says_so(app, client, user_id, clock):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0)))
        db.session.commit()
    client.post("/timer/start", data={"text": "Audit"})
    clock.set(2026, 11, 12, 15, 0)
    client.post("/timer/stop")  # a new line: not a change of times
    assert log(app) == []
    client.post("/timer/start", data={"text": "Audit"})
    clock.set(2026, 11, 12, 16, 0)
    client.post("/timer/stop")  # straight on with the same words: the line grows
    assert log(app) == [("Line end", "15:00", "16:00", "timer")]


def test_marked_by_hand(app, client, user_id):
    client.post(f"/entry/{WED}", data={"login_time": "09:00", "logout_time": "18:00"})
    with app.app_context():
        assert changes.edited_days(user_id, dt.date(2026, 11, 1), dt.date(2026, 11, 30)) == {WED}
    history = client.get("/history/2026/11").get_data(as_text=True)
    assert history.count("badge-hand") == 1
    cal = client.get("/calendar/2026/11").get_data(as_text=True)
    assert "times typed by hand" in cal and cal.count('class="cal-hand"') == 1
    editor = client.get(f"/entry/{WED}").get_data(as_text=True)
    assert "Changes" in editor and "6:00 PM" in editor and "By hand" in editor


def test_leave_is_not_by_hand(app, client, user_id):
    client.post(f"/entry/{WED}", data={"day_type": "full"})
    assert log(app) == [("Target", "", "0h", "edit")]
    with app.app_context():
        assert changes.edited_days(user_id, WED, WED) == set()


def test_excel_column(app, client, user_id):
    pytest.importorskip("xlsxwriter")
    client.post(f"/entry/{WED}", data={"login_time": "09:00", "logout_time": "18:00"})
    import app.exports as exports
    import app.main as main
    with app.test_request_context():
        from flask_login import login_user
        from app.models import User
        login_user(db.session.get(User, user_id))
        page = main._month_page(2026, 11)
        report = exports.month_report(page, db.session.get(User, user_id), dt.datetime(2026, 11, 12, 14, 0))
    marked = [d["date"] for d in report["days"] if d["by_hand"]]
    assert marked == [WED]
