"""7.3 Public holidays: the built-in Indian list (the office's own for 2026,
only the national three for other years), lines pasted in, or one at a
time. A holiday sets its day's target to 0 unless the day has its own --
and the leave calendar feed, behind a secret in its address."""
import datetime as dt

import pytest

from app import db, holidays, logic
from app.models import Holiday, TimeEntry, User

T = dt.time
D = dt.date


def test_the_builtin_lists(app):
    with app.app_context():
        offered, about = holidays.builtin(2026)
        assert len(offered) == 13 and all(ticked for *_, ticked in offered)
        assert (D(2026, 11, 1), "Karnataka Rajyotsava", True) in offered
        assert about["checked"] == "14 Nov 2026" and "Office holiday list" in about["source"]
        offered, about = holidays.builtin(2027)
        assert about is None
        assert offered == [(D(2027, 1, 26), "Republic Day", True), (D(2027, 8, 15), "Independence Day", True),
                           (D(2027, 10, 2), "Gandhi Jayanti", True)]


def test_pasted_lines():
    found, bad = holidays.parse_lines("2026-12-25, Christmas\n\n31/12/2026,  New   Year's Eve \n"
                                      "26 Jan 2027\tRepublic Day\nnonsense\n2026-13-01, No such day\n2026-12-01,")
    assert found == [(D(2026, 12, 25), "Christmas"), (D(2026, 12, 31), "New Year's Eve"),
                     (D(2027, 1, 26), "Republic Day")]
    assert bad == ["nonsense", "2026-13-01, No such day", "2026-12-01,"]


@pytest.fixture
def days(app, user_id):
    """Diwali already worked (no target of its own), Christmas on half-day leave."""
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 11, 8), login_time=T(10, 0), logout_time=T(14, 0)))
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 12, 25), target_override=4.0, leave_type="half",
                                 leave_label="Half day"))
        db.session.commit()


def entry(app, user_id, day):
    with app.app_context():
        e = TimeEntry.query.filter_by(user_id=user_id, date=day).first()
        return None if e is None else (e.login_time, e.target_override, e.leave_type, e.leave_label)


def test_saved_holidays_set_the_target(app, client, user_id, days):
    picks = ["2026-11-08", "2026-12-25", "2026-10-02", "2099-01-01"]
    r = client.post("/holidays/add", data={"how": "builtin", "year": "2026", "pick": picks}, follow_redirects=True)
    assert "Saved 3 holidays." in r.get_data(as_text=True)
    assert entry(app, user_id, D(2026, 10, 2)) == (None, 0.0, "full", "Gandhi Jayanti")
    assert entry(app, user_id, D(2026, 11, 8)) == (T(10, 0), 0.0, "full", "Diwali")  # all of it overtime now
    assert entry(app, user_id, D(2026, 12, 25)) == (None, 4.0, "half", "Half day")  # its own target stays
    with app.app_context():
        user = db.session.get(User, user_id)
        assert logic.target_hours_for(user, D(2026, 10, 2), TimeEntry.query.filter_by(date=D(2026, 10, 2)).one()) == 0
        applied = {h.date: h.applied for h in Holiday.query}
    assert applied == {D(2026, 10, 2): True, D(2026, 11, 8): True, D(2026, 12, 25): False}
    page = client.get("/holidays?year=2026").get_data(as_text=True)
    assert "Gandhi Jayanti" in page and "Own hours" in page
    assert "Diwali" in client.get("/history/2026/11").get_data(as_text=True)


def test_removed_puts_the_day_back(app, client, user_id, days):
    client.post("/holidays/add", data={"how": "builtin", "year": "2026",
                                       "pick": ["2026-11-08", "2026-12-25", "2026-10-02"]})
    with app.app_context():
        ids = {h.date: h.id for h in Holiday.query}
    for day in ids.values():
        client.post(f"/holidays/{day}/delete")
    assert entry(app, user_id, D(2026, 10, 2)) is None
    assert entry(app, user_id, D(2026, 11, 8)) == (T(10, 0), None, None, None)
    assert entry(app, user_id, D(2026, 12, 25)) == (None, 4.0, "half", "Half day")
    with app.app_context():
        assert Holiday.query.count() == 0


def test_one_and_pasted(app, client, user_id):
    r = client.post("/holidays/add", data={"how": "one", "date": "2027-03-10", "name": "Office day"},
                    follow_redirects=True)
    assert "Saved 1 holiday." in r.get_data(as_text=True) and r.request.args.get("year") == "2027"
    r = client.post("/holidays/add", data={"how": "paste", "lines": "2027-03-10, Office day renamed\nrubbish"},
                    follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Saved 0 holidays (1 already saved)." in text and "Couldn&#39;t read 1 line: rubbish" in text
    assert entry(app, user_id, D(2027, 3, 10)) == (None, 0.0, "full", "Office day renamed")
    r = client.post("/holidays/add", data={"how": "one", "date": "", "name": "x"}, follow_redirects=True)
    assert "Give the holiday a date and a name." in r.get_data(as_text=True)
    assert client.post("/holidays/add", data={"how": "sideways"}).status_code == 400


def test_someone_elses(app, client, user_id):
    with app.app_context():
        other = User(username="other")
        other.set_password("password1")
        db.session.add(other)
        db.session.flush()
        theirs = Holiday(user_id=other.id, date=D(2026, 12, 25), name="Theirs", applied=False)
        db.session.add(theirs)
        db.session.commit()
        theirs_id = theirs.id
    assert client.post(f"/holidays/{theirs_id}/delete").status_code == 404


def test_the_leave_calendar(app, client, user_id, days):
    assert client.get("/calendar/leave.ics?key=nope").status_code == 404
    client.post("/holidays/add", data={"how": "builtin", "year": "2026", "pick": ["2026-10-02"]})
    client.post("/holidays/feed")
    with app.app_context():
        token = db.session.get(User, user_id).ics_token
    assert token and len(token) >= 30
    page = client.get("/holidays").get_data(as_text=True)
    assert f"/calendar/leave.ics?key={token}" in page
    r = app.test_client().get(f"/calendar/leave.ics?key={token}")  # no sign-in: the key is enough
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20261002\r\nDTEND;VALUE=DATE:20261003\r\nSUMMARY:Gandhi Jayanti" in body
    assert "SUMMARY:Half day" in body and body.count("BEGIN:VEVENT") == 2
    # A new link: the old one stops.
    client.post("/holidays/feed")
    assert app.test_client().get(f"/calendar/leave.ics?key={token}").status_code == 404


def test_long_lines_fold():
    line = "SUMMARY:" + "Ganesh Chaturthi " * 8
    folded = holidays._fold(line)
    assert all(len(part.encode()) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line
