"""Task suggestions: the words of the last 60 days' lines, up to 50, in the
page's list; app.js shows three at a time as you type."""
import datetime as dt
import re

from app import db
from app.models import TimeEntry, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)


def options(page, list_id):
    found = re.search(r'<datalist id="%s">(.*?)</datalist>' % list_id, page, re.S)
    return re.findall(r'<option value="([^"]*)">', found.group(1)) if found else []


def test_fifty_latest_from_the_last_60_days(app, client, user_id):
    with app.app_context():
        for n in range(60):
            db.session.add(WorkBlock(user_id=user_id, date=THU - dt.timedelta(days=n), start_time=T(9, 0),
                                     end_time=T(9, 30), text=f"Task {n}"))
        db.session.add(WorkBlock(user_id=user_id, date=THU, start_time=T(10, 0), end_time=T(10, 30), text="Break"))
        db.session.add(WorkBlock(user_id=user_id, date=THU - dt.timedelta(days=61), start_time=T(9, 0),
                                 end_time=T(9, 30), text="Too old"))
        db.session.commit()
    got = options(client.get("/timesheet").get_data(as_text=True), "ts-recent")
    assert len(got) == 50
    assert got[:2] == ["Task 0", "Task 1"] and "Break" not in got and "Too old" not in got


def test_every_list_leaves_out_the_running_task(app, client, clock, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0)))
        for n, text in enumerate(("VAT return", "Payroll")):
            db.session.add(WorkBlock(user_id=user_id, date=THU - dt.timedelta(days=n + 1), start_time=T(9, 0),
                                     end_time=T(9, 30), text=text))
        db.session.commit()
    clock.set(2026, 11, 12, 10, 0)
    client.post("/timer/start", data={"text": "VAT return"})
    clock.set(2026, 11, 12, 10, 5)
    sheet = client.get("/timesheet").get_data(as_text=True)
    for list_id in ("ts-recent", "tb-recent-sheet", "timer-recent"):
        assert options(sheet, list_id) == ["Payroll"]
    assert options(client.get("/dashboard").get_data(as_text=True), "timer-recent") == ["Payroll"]
    assert options(client.get("/calendar/week").get_data(as_text=True), "cg-recent") == ["Payroll"]


def test_each_task_box_has_its_list(app, client, user_id):
    with app.app_context():
        db.session.add(WorkBlock(user_id=user_id, date=THU, start_time=T(9, 0), end_time=T(9, 30), text="VAT return"))
        db.session.commit()
    sheet = client.get("/timesheet").get_data(as_text=True)
    # The Timesheet's words are a growing textarea, which names its list in data-suggest.
    assert 'data-suggest="ts-recent"' in sheet and options(sheet, "ts-recent") == ["VAT return"]
    calendar = client.get("/calendar/week").get_data(as_text=True)
    assert 'list="cg-recent"' in calendar and options(calendar, "cg-recent") == ["VAT return"]
