"""4.2 Copy last week and week templates: lines put on this week's free time
-- days already worked, clipped to their hours, around breaks and around
every line already there -- so doing it twice adds nothing."""
import datetime as dt
import json

import pytest

from app import db, logic
from app.models import BreakSegment, TimeEntry, User, WeekTemplate, WorkBlock

T = dt.time
D = dt.date
WEEK = "2026-11-09"  # Monday; the clock stands at Thursday 12 Nov, 2 PM


class TestFreePieces:
    def test_cut_out_of_the_middle(self):
        assert logic.free_pieces(540, 720, [(600, 630)]) == [(540, 600), (630, 720)]

    def test_nothing_taken(self):
        assert logic.free_pieces(540, 600, []) == [(540, 600)]

    def test_all_taken(self):
        assert logic.free_pieces(540, 600, [(500, 700)]) == []

    def test_edges_and_several(self):
        assert logic.free_pieces(540, 720, [(540, 560), (700, 800), (600, 610)]) == [(560, 600), (610, 700)]

    def test_empty(self):
        assert logic.free_pieces(600, 600, []) == []


def add_day(user_id, day, login, logout=None, lines=(), breaks=()):
    entry = TimeEntry(user_id=user_id, date=day, login_time=login, logout_time=logout)
    db.session.add(entry)
    db.session.flush()
    for a, b in breaks:
        db.session.add(BreakSegment(entry_id=entry.id, break_start=a, break_end=b))
    for a, b, text in lines:
        db.session.add(WorkBlock(user_id=user_id, date=day, start_time=a, end_time=b, text=text))


@pytest.fixture
def weeks(app, user_id):
    """Last week: Monday and Thursday written. This week: Monday 10 to 5
    with a half-hour break and one line written; Thursday (today) logged
    in at 9 and still going."""
    with app.app_context():
        add_day(user_id, D(2026, 11, 2), T(9, 0), T(18, 0), lines=[
            (T(9, 0), T(10, 0), "Emails"), (T(10, 0), T(12, 0), "VAT return"),
            (T(12, 0), T(13, 0), "Break"), (T(13, 0), T(18, 0), "Accounts")])
        add_day(user_id, D(2026, 11, 5), T(9, 0), T(17, 0), lines=[(T(9, 0), T(17, 0), "Audit")])
        add_day(user_id, D(2026, 11, 6), T(9, 0), T(17, 0), lines=[(T(9, 0), T(17, 0), "Friday work")])
        add_day(user_id, D(2026, 11, 9), T(10, 0), T(17, 0), breaks=[(T(13, 0), T(13, 30))],
                lines=[(T(14, 0), T(15, 0), "Client call")])
        add_day(user_id, D(2026, 11, 12), T(9, 0))
        db.session.commit()


def lines(app, user_id, day):
    with app.app_context():
        return sorted((b.start_time.strftime("%H:%M"), b.end_time.strftime("%H:%M"), b.text)
                      for b in WorkBlock.query.filter_by(user_id=user_id, date=day))


def test_copy_last_week(app, client, user_id, weeks):
    r = client.post(f"/timesheet/week/{WEEK}/copy-last", follow_redirects=True)
    assert "Added 4 lines from last week." in r.get_data(as_text=True)
    # Clipped to Monday's hours, around the break and the line written.
    assert lines(app, user_id, D(2026, 11, 9)) == [
        ("10:00", "12:00", "VAT return"), ("13:30", "14:00", "Accounts"),
        ("14:00", "15:00", "Client call"), ("15:00", "17:00", "Accounts")]
    # Today: up to now, not the rest of the day.
    assert lines(app, user_id, D(2026, 11, 12)) == [("09:00", "14:00", "Audit")]
    # Friday is still to come; Tuesday wasn't worked.
    assert lines(app, user_id, D(2026, 11, 13)) == []
    assert lines(app, user_id, D(2026, 11, 10)) == []


def test_copy_last_week_twice_adds_nothing(app, client, user_id, weeks):
    client.post(f"/timesheet/week/{WEEK}/copy-last")
    before = lines(app, user_id, D(2026, 11, 9))
    r = client.post(f"/timesheet/week/{WEEK}/copy-last", follow_redirects=True)
    assert "Nothing added from last week" in r.get_data(as_text=True)
    assert lines(app, user_id, D(2026, 11, 9)) == before


def test_deleted_time_stays_deleted(app, client, user_id, weeks):
    with app.app_context():
        db.session.add(WorkBlock(user_id=user_id, date=D(2026, 11, 9), start_time=T(10, 30), end_time=T(11, 0), text=""))
        db.session.commit()
    client.post(f"/timesheet/week/{WEEK}/copy-last")
    got = [x for x in lines(app, user_id, D(2026, 11, 9)) if x[2] == "VAT return"]
    assert got == [("10:00", "10:30", "VAT return"), ("11:00", "12:00", "VAT return")]


def test_scraps_under_ten_minutes_stay_free(app, client, user_id):
    with app.app_context():
        add_day(user_id, D(2026, 11, 2), T(9, 0), T(18, 0), lines=[(T(9, 0), T(18, 0), "Audit")])
        add_day(user_id, D(2026, 11, 9), T(9, 0), T(10, 0), lines=[(T(9, 5), T(10, 0), "Post")])
        db.session.commit()
    client.post(f"/timesheet/week/{WEEK}/copy-last")
    assert lines(app, user_id, D(2026, 11, 9)) == [("09:05", "10:00", "Post")]


def test_nothing_last_week(app, client, user_id):
    r = client.post(f"/timesheet/week/{WEEK}/copy-last", follow_redirects=True)
    assert "Last week has no lines with words to copy." in r.get_data(as_text=True)


def test_save_and_use_a_template(app, client, user_id, weeks):
    r = client.post("/timesheet/week/2026-11-02/template", data={"name": "  Usual   week "}, follow_redirects=True)
    assert "Saved 5 lines as “Usual week”." in r.get_data(as_text=True)
    with app.app_context():
        template = WeekTemplate.query.filter_by(user_id=user_id).one()
        assert template.name == "Usual week"
        assert json.loads(template.lines)[0] == {"weekday": 0, "start": 540, "end": 600, "text": "Emails"}
        template_id = template.id
    r = client.post(f"/timesheet/week/{WEEK}/template/use", data={"template": template_id}, follow_redirects=True)
    assert "Added 4 lines from “Usual week”." in r.get_data(as_text=True)
    once = lines(app, user_id, D(2026, 11, 9))
    # Twice: no line twice.
    r = client.post(f"/timesheet/week/{WEEK}/template/use", data={"template": template_id}, follow_redirects=True)
    assert "Nothing added from “Usual week”" in r.get_data(as_text=True)
    assert lines(app, user_id, D(2026, 11, 9)) == once
    assert len(once) == len(set(once))


def test_same_name_replaces(app, client, user_id, weeks):
    client.post("/timesheet/week/2026-11-02/template", data={"name": "Usual"})
    client.post(f"/timesheet/week/{WEEK}/template", data={"name": "Usual"})
    with app.app_context():
        template = WeekTemplate.query.filter_by(user_id=user_id).one()
        assert [line["text"] for line in json.loads(template.lines)] == ["Client call"]


def test_a_week_with_nothing_to_keep(app, client, user_id):
    r = client.post(f"/timesheet/week/{WEEK}/template", data={"name": "Empty"}, follow_redirects=True)
    assert "This week has no lines with words to keep." in r.get_data(as_text=True)
    with app.app_context():
        assert WeekTemplate.query.count() == 0


def test_someone_elses_template(app, client, user_id, weeks):
    with app.app_context():
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        theirs = WeekTemplate(user_id=other.id, name="Theirs",
                              lines=json.dumps([{"weekday": 0, "start": 600, "end": 700, "text": "Theirs"}]))
        db.session.add(theirs)
        db.session.commit()
        theirs_id = theirs.id
    assert client.post(f"/timesheet/week/{WEEK}/template/use", data={"template": theirs_id}).status_code == 404
    assert client.post(f"/timesheet/week/{WEEK}/template/delete", data={"template": theirs_id}).status_code == 404
    assert lines(app, user_id, D(2026, 11, 9)) == [("14:00", "15:00", "Client call")]


def test_a_damaged_template_adds_only_what_it_can_read(app, client, user_id, weeks):
    with app.app_context():
        damaged = WeekTemplate(user_id=user_id, name="Damaged", lines=json.dumps([
            {"weekday": 0, "start": 600, "end": 660, "text": "Good"}, {"weekday": 9, "start": 1, "end": 2, "text": "x"},
            {"weekday": 0, "start": 700, "end": 650, "text": "Backwards"}, {"text": "No times"}, "not a line",
            {"weekday": 0, "start": 900, "end": 960, "text": "   "}]))
        db.session.add(damaged)
        broken = WeekTemplate(user_id=user_id, name="Broken", lines="{not json")
        db.session.add(broken)
        db.session.commit()
        damaged_id, broken_id = damaged.id, broken.id
    client.post(f"/timesheet/week/{WEEK}/template/use", data={"template": damaged_id})
    assert ("10:00", "11:00", "Good") in lines(app, user_id, D(2026, 11, 9))
    r = client.post(f"/timesheet/week/{WEEK}/template/use", data={"template": broken_id})
    assert r.status_code == 302


def test_delete_a_template(app, client, user_id, weeks):
    client.post("/timesheet/week/2026-11-02/template", data={"name": "Usual"})
    with app.app_context():
        template_id = WeekTemplate.query.one().id
    r = client.post(f"/timesheet/week/{WEEK}/template/delete", data={"template": template_id}, follow_redirects=True)
    assert "Deleted the template “Usual”." in r.get_data(as_text=True)
    with app.app_context():
        assert WeekTemplate.query.count() == 0


def test_week_page_tools(app, client, user_id, weeks):
    client.post("/timesheet/week/2026-11-02/template", data={"name": "Usual"})
    page = client.get(f"/timesheet/week/{WEEK}").get_data(as_text=True)
    assert "Copy last week" in page and "Use template" in page and ">Usual</option>" in page
    assert "Save as template" in page
    # A week still to come has nothing to fill.
    later = client.get("/timesheet/week/2026-11-16").get_data(as_text=True)
    assert "Copy last week" not in later and "Use template" not in later


def test_bad_week(client):
    assert client.post("/timesheet/week/nonsense/copy-last").status_code == 400
