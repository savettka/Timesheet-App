"""1.4 A line's menu: mark posted or not, duplicate, split in two and
delete, from Boards and the Calendar (POST /line/<id>/<action>) and from the
Timesheet's own form (split= and duplicate= by the line's place)."""
import datetime as dt
import re

import pytest

from app import db, logic
from app.models import TimeEntry, User, WorkBlock

T = dt.time
WED = dt.date(2026, 11, 11)


class TestWhereLinesGo:
    def test_split_at_the_middle_on_five_minutes(self):
        assert logic.split_point(540, 600) == 570
        assert logic.split_point(540, 555) == 550  # 9:00-9:15: 9:07½ rounds to 9:10, five clear of the end
        assert logic.split_point(540, 549) is None

    def test_a_duplicate_goes_straight_after(self):
        assert logic.duplicate_span(540, 600, 1440) == (600, 660)

    def test_cut_at_the_end_of_the_day(self):
        assert logic.duplicate_span(1380, 1430, 1440) == (1430, 1440)
        assert logic.duplicate_span(1380, 1440, 1440) is None


@pytest.fixture
def day(app, user_id):
    """Wednesday 9 AM to 6 PM, with two lines written."""
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 0), logout_time=T(18, 0)))
        first = WorkBlock(user_id=user_id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="VAT return")
        second = WorkBlock(user_id=user_id, date=WED, start_time=T(10, 0), end_time=T(11, 30), text="Payroll")
        db.session.add_all([first, second])
        db.session.commit()
        return first.id, second.id


def lines(app, user_id, date=WED):
    with app.app_context():
        return sorted((b.start_time.strftime("%H:%M"), b.end_time.strftime("%H:%M"), b.text)
                      for b in WorkBlock.query.filter_by(user_id=user_id, date=date))


def test_mark_and_unmark(app, client, day, user_id):
    first, _ = day
    r = client.post(f"/line/{first}/mark", headers={"Accept": "application/json"})
    assert r.get_json() == {"ok": True, "message": "Marked 9:00–10:00 AM as posted.", "copied": True}
    with app.app_context():
        assert db.session.get(WorkBlock, first).is_copied
    r = client.post(f"/line/{first}/unmark", data={"back": "/calendar/week"})
    assert r.headers["Location"].endswith("/calendar/week")
    with app.app_context():
        assert not db.session.get(WorkBlock, first).is_copied


def test_split_in_two(app, client, day, user_id):
    first, _ = day
    r = client.post(f"/line/{first}/split", data={"back": "/boards"}, follow_redirects=True)
    assert "Split 9:00–10:00 AM in two" in r.get_data(as_text=True)
    assert lines(app, user_id)[:2] == [("09:00", "09:30", "VAT return"), ("09:30", "10:00", "VAT return")]


def test_duplicate_never_covers_words(app, client, day, user_id):
    first, _ = day
    r = client.post(f"/line/{first}/duplicate", headers={"Accept": "application/json"})
    assert r.get_json()["ok"] is False and "no free time" in r.get_json()["message"]
    assert lines(app, user_id) == [("09:00", "10:00", "VAT return"), ("10:00", "11:30", "Payroll")]


def test_duplicate_takes_the_free_time_after_it(app, client, day, user_id):
    _, second = day
    with app.app_context():
        db.session.add(WorkBlock(user_id=user_id, date=WED, start_time=T(12, 30), end_time=T(13, 0), text="Call"))
        db.session.commit()
    client.post(f"/line/{second}/duplicate")
    # 1h 30m long, but only an hour free before the 12:30 call.
    assert ("11:30", "12:30", "Payroll") in lines(app, user_id)
    assert ("12:30", "13:00", "Call") in lines(app, user_id)


def test_a_deleted_line_is_free_time(app, client, user_id):
    with app.app_context():
        first = WorkBlock(user_id=user_id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="VAT return")
        db.session.add_all([first, WorkBlock(user_id=user_id, date=WED, start_time=T(10, 0), end_time=T(11, 0), text="")])
        db.session.commit()
        first_id = first.id
    client.post(f"/line/{first_id}/duplicate")
    assert lines(app, user_id) == [("09:00", "10:00", "VAT return"), ("10:00", "11:00", "VAT return")]


def test_no_room_to_duplicate_at_midnight(app, client, user_id):
    with app.app_context():
        late = WorkBlock(user_id=user_id, date=WED, start_time=T(23, 0), end_time=T(0, 0), text="Late")
        db.session.add(late)
        db.session.commit()
        late_id = late.id
    r = client.post(f"/line/{late_id}/duplicate", headers={"Accept": "application/json"})
    assert r.get_json()["ok"] is False and "no free time" in r.get_json()["message"]
    assert lines(app, user_id) == [("23:00", "00:00", "Late")]


def test_duplicate_past_midnight_on_a_late_shift(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(15, 0), logout_time=T(1, 0)))
        late = WorkBlock(user_id=user_id, date=WED, start_time=T(23, 0), end_time=T(0, 0), text="Late")
        db.session.add(late)
        db.session.commit()
        late_id = late.id
    client.post(f"/line/{late_id}/duplicate")
    assert ("00:00", "01:00", "Late") in lines(app, user_id)
    page = client.get("/timesheet/2026-11-11").get_data(as_text=True)
    assert page.index('value="23:00"') < page.index('value="00:00" aria-label="From')


def test_delete_keeps_the_time_taken(app, client, day, user_id):
    _, second = day
    client.post(f"/line/{second}/delete")
    assert lines(app, user_id) == [("09:00", "10:00", "VAT return"), ("10:00", "11:30", "")]
    assert client.post(f"/line/{second}/mark").status_code == 404  # deleted: nothing more to do to it


def test_only_your_own_lines_and_known_actions(app, client, day, user_id):
    first, _ = day
    with app.app_context():
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        theirs = WorkBlock(user_id=other.id, date=WED, start_time=T(9, 0), end_time=T(10, 0), text="Theirs")
        db.session.add(theirs)
        db.session.commit()
        theirs_id = theirs.id
    assert client.post(f"/line/{theirs_id}/delete").status_code == 404
    assert client.post(f"/line/{first}/explode").status_code == 404
    assert client.get(f"/line/{first}/split").status_code == 405


def test_never_back_to_another_site(client, day):
    first, _ = day
    r = client.post(f"/line/{first}/mark", data={"back": "https://evil.example/"})
    assert r.headers["Location"].endswith("/timesheet/2026-11-11")


def form_of(page):
    """The Timesheet's lines, as its form would send them."""
    data = {k: [] for k in ("id", "start", "end", "text", "was_start", "was_end", "was_text")}
    for li in re.findall(r'<li class="ts-line[^"]*"[^>]*>(.*?)</li>', page, re.S):
        for k in data:
            # The words sit in a textarea (it grows to show a long entry).
            box = re.search(rf'<textarea name="{k}"[^>]*>(.*?)</textarea>', li, re.S)
            data[k].append(box.group(1) if box else re.search(rf'name="{k}"[^>]*?value="([^"]*)"', li).group(1))
    return data


def test_split_and_duplicate_from_the_timesheet(app, client, day, user_id):
    page = client.get("/timesheet/2026-11-11").get_data(as_text=True)
    data = form_of(page)
    data["text"][0] = "VAT return for Smith Ltd"  # an edit not saved yet, then Split
    r = client.post("/timesheet/2026-11-11", data=dict(data, split="0"), follow_redirects=True)
    assert "Split 9:00–10:00 AM in two" in r.get_data(as_text=True)
    assert lines(app, user_id)[:2] == [("09:00", "09:30", "VAT return for Smith Ltd"),
                                       ("09:30", "10:00", "VAT return for Smith Ltd")]
    data = form_of(r.get_data(as_text=True))
    r = client.post("/timesheet/2026-11-11", data=dict(data, duplicate="2"), follow_redirects=True)
    assert "Duplicated 10:00–11:30 AM at 11:30 AM–1:00 PM" in r.get_data(as_text=True)
    assert ("11:30", "13:00", "Payroll") in lines(app, user_id)


def test_the_menu_has_what_it_needs(client, day):
    page = client.get("/timesheet/2026-11-11").get_data(as_text=True)
    assert page.count('class="btn btn-icon ts-more"') == len(re.findall(r'<li class="ts-line', page))
    board = client.get("/boards?week=2026-11-11").get_data(as_text=True)
    assert 'data-copy="VAT return (09:00 to 10:00 AM)"' in board and 'class="kb-more"' in board
    grid = client.get("/calendar/week/2026-11-11").get_data(as_text=True)
    assert 'data-copy="Payroll (10:00 to 11:30 AM)"' in grid and 'id="cg-more"' in grid
