"""4.1 Batch: tick boxes on the Timesheet's lines (Day, Week, All) and one
route for the ticked ones -- POST /timesheet/batch with their ids and an
action: copy (marked posted after copying), mark, unmark or delete."""
import datetime as dt
import json
import re

import pytest

from app import db
from app.models import TimeEntry, User, WorkBlock

T = dt.time
WED = dt.date(2026, 11, 11)


@pytest.fixture
def day(app, user_id):
    """Wednesday 9 AM to 6 PM: two lines written, one written as Break, and
    a line of someone else's."""
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 0), logout_time=T(18, 0)))
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        made = [WorkBlock(user_id=user_id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="VAT return"),
                WorkBlock(user_id=user_id, date=WED, start_time=T(10, 0), end_time=T(11, 30), text="Payroll"),
                WorkBlock(user_id=user_id, date=WED, start_time=T(11, 30), end_time=T(12, 0), text="Break"),
                WorkBlock(user_id=other.id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="Theirs")]
        db.session.add_all(made)
        db.session.commit()
        return [b.id for b in made]


def state(app, block_id):
    with app.app_context():
        b = db.session.get(WorkBlock, block_id)
        return b.text, b.is_copied


def batch(client, ids, action, back="/timesheet/all", **kw):
    return client.post("/timesheet/batch", data={"ids": ids, "action": action, "back": back}, **kw)


def test_mark_then_unmark(app, client, day):
    vat, payroll, *_ = day
    r = batch(client, f"{vat},{payroll}", "mark", back="/timesheet/2026-11-11", follow_redirects=True)
    assert r.request.path == "/timesheet/2026-11-11"
    assert "Marked 2 lines as posted." in r.get_data(as_text=True)
    assert state(app, vat) == ("VAT return", True) and state(app, payroll) == ("Payroll", True)
    r = batch(client, str(vat), "unmark", follow_redirects=True)
    assert "1 line no longer marked as posted." in r.get_data(as_text=True)
    assert state(app, vat) == ("VAT return", False) and state(app, payroll) == ("Payroll", True)


def test_copy_marks_them_too(app, client, day):
    vat, payroll, *_ = day
    r = batch(client, f"{vat},{payroll}", "copy", follow_redirects=True)
    assert "Copied 2 lines and marked them as posted." in r.get_data(as_text=True)
    assert state(app, vat)[1] and state(app, payroll)[1]


def test_delete_keeps_the_time_taken(app, client, day):
    vat, payroll, *_ = day
    r = batch(client, f"{vat},{payroll}", "delete", follow_redirects=True)
    assert "Deleted 2 lines." in r.get_data(as_text=True)
    # Kept with no words, as one line's Delete does: not offered again.
    assert state(app, vat) == ("", False) and state(app, payroll) == ("", False)
    page = client.get("/timesheet/2026-11-11").get_data(as_text=True)
    assert 'value="09:00"' not in page


def test_ids_split_over_values_and_said_twice(app, client, day):
    vat, payroll, *_ = day
    r = client.post("/timesheet/batch", data={"ids": [f"{vat},{payroll}", str(vat)], "action": "mark"},
                    follow_redirects=True)
    assert "Marked 2 lines as posted." in r.get_data(as_text=True)


def test_only_your_own_lines_with_words(app, client, day):
    vat, _, brk, theirs = day
    r = batch(client, f"{brk},{theirs},999999,x", "delete", follow_redirects=True)
    assert "None of those lines are there any more." in r.get_data(as_text=True)
    assert state(app, brk) == ("Break", False)
    assert state(app, theirs) == ("Theirs", False)


def test_refused(app, client, day):
    vat, *_ = day
    assert batch(client, str(vat), "explode").status_code == 400
    assert batch(client, ",".join(str(n) for n in range(1, 502)), "mark").status_code == 400
    # Never back to another site.
    r = batch(client, str(vat), "mark", back="//elsewhere.example/")
    assert r.headers["Location"].endswith("/timesheet/all")


def test_signed_out(app, day):
    r = app.test_client().post("/timesheet/batch", data={"ids": "1", "action": "delete"})
    assert r.status_code in (302, 401)
    assert state(app, day[0]) == ("VAT return", False)


def test_tick_boxes_on_the_pages(app, client, day):
    vat, payroll, *_ = day
    page = client.get("/timesheet/2026-11-11").get_data(as_text=True)
    assert 'id="bb"' in page and 'name="action"' in page
    # A box on each line but the break; one on a line with no words is off.
    boxes = re.findall(r'<input type="checkbox" class="js-pick"[^>]*>', page)
    lines = page.count('<li class="ts-line')
    breaks = page.count(' is-break')
    assert len(boxes) == lines - breaks
    assert sum("disabled" in b for b in boxes) == lines - breaks - 2

    week = client.get("/timesheet/week/2026-11-09").get_data(as_text=True)
    assert f'data-ids="{vat}"' in week and f'data-ids="{payroll}"' in week

    every = client.get("/timesheet/all?before=2026-11-12").get_data(as_text=True)
    found = re.search(rf"data-ids=\"{vat}\" data-lines='([^']*)'", every)
    assert json.loads(found.group(1).replace("&#34;", '"')) == [
        ["2026-11-11", "Wed 11 Nov", 540, "VAT return (09:00 to 10:00 AM)"]]
