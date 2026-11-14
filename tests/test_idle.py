"""6.2 The idle prompt: back after time away from a running timer, keep it,
make it a break (the line splits around it), or drop it (the line ends
where the time away began). POST /timer/idle with `away` seconds."""
import datetime as dt
import json

import pytest

from app import db
from app.models import BreakSegment, TimeEntry, User, WorkBlock

T = dt.time
THU = dt.date(2026, 11, 12)  # the clock fixture's day, 2 PM


@pytest.fixture
def timing(app, user_id):
    """Logged in at 9, with Audit timed since noon."""
    with app.app_context():
        user = db.session.get(User, user_id)
        user.timer_text, user.timer_start = "Audit", dt.datetime(2026, 11, 12, 12, 0)
        user.reminder_settings = json.dumps({"idle": {"on": True, "minutes": 20}})
        db.session.add(TimeEntry(user_id=user_id, date=THU, login_time=T(9, 0)))
        db.session.commit()


def idle(client, action, away):
    return client.post("/timer/idle", data={"action": action, "away": str(away)}).get_json()


def state(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        lines = sorted((b.start_time.strftime("%H:%M"), b.end_time.strftime("%H:%M"), b.text)
                       for b in WorkBlock.query.filter_by(user_id=user_id))
        breaks = [(b.break_start.strftime("%H:%M"), b.break_end.strftime("%H:%M")) for b in BreakSegment.query]
        return lines, breaks, user.timer_text, user.timer_start


def test_keep(app, client, user_id, timing):
    assert idle(client, "keep", 1800) == {"ok": True, "message": "Kept on the timer."}
    assert state(app, user_id) == ([], [], "Audit", dt.datetime(2026, 11, 12, 12, 0))


def test_a_break(app, client, user_id, timing):
    r = idle(client, "break", 1800)
    assert r == {"ok": True, "message": "Made the 30m away a break, 1:30 PM to 2:00 PM."}
    assert state(app, user_id) == ([("12:00", "13:30", "Audit")], [("13:30", "14:00")],
                                   "Audit", dt.datetime(2026, 11, 12, 14, 0))


def test_dropped(app, client, user_id, timing):
    r = idle(client, "drop", 1800)
    assert r == {"ok": True, "message": "Dropped the 30m away: Audit ends at 1:30 PM."}
    assert state(app, user_id) == ([("12:00", "13:30", "Audit")], [], None, None)


def test_never_before_the_timer_or_the_last_break(app, client, user_id, timing):
    with app.app_context():
        entry = TimeEntry.query.one()
        db.session.add(BreakSegment(entry_id=entry.id, break_start=T(13, 0), break_end=T(13, 10)))
        db.session.commit()
    idle(client, "break", 5 * 3600)
    lines, breaks, *_ = state(app, user_id)
    assert lines == [("12:00", "13:10", "Audit")] and breaks == [("13:00", "13:10"), ("13:10", "14:00")]


def test_refused(app, client, user_id, timing):
    assert client.post("/timer/idle", data={"action": "nap", "away": "60"}).status_code == 400
    assert client.post("/timer/idle", data={"action": "drop", "away": "-5"}).status_code == 400
    assert idle(client, "drop", 20)["ok"] is False  # under a minute
    with app.app_context():
        db.session.get(User, user_id).timer_start = None
        db.session.commit()
    assert idle(client, "drop", 1800) == {"ok": False, "message": "The timer isn't running any more."}


def test_the_prompt_is_on_the_page_only_while_timing(app, client, user_id, timing):
    page = client.get("/timesheet").get_data(as_text=True)
    assert 'id="idle-dialog"' in page and 'data-minutes="20"' in page and "Keep it on Audit" in page
    with app.app_context():
        db.session.get(User, user_id).reminder_settings = None  # the default: off
        db.session.commit()
    assert 'id="idle-dialog"' not in client.get("/timesheet").get_data(as_text=True)
