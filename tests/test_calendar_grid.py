"""Phase 2, the Calendar: zoom, the line for now, and moving a line by its
top, its bottom or the whole of it -- through POST /calendar/line, with the
lines either side following, the same times then on the Timesheet."""
import datetime as dt
import re

import pytest

from app import db
from app.models import TimeEntry, WorkBlock

T = dt.time
WED = dt.date(2026, 11, 11)


@pytest.fixture
def three(app, user_id):
    """Wednesday 9 AM to 6 PM: 9-10, 10-11 and 11-12 written."""
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(9, 0), logout_time=T(18, 0)))
        made = [WorkBlock(user_id=user_id, date=WED, start_time=T(h, 0), end_time=T(h + 1, 0), text=text)
                for h, text in ((9, "VAT"), (10, "Payroll"), (11, "Emails"))]
        db.session.add_all(made)
        db.session.commit()
        return [b.id for b in made]


def lines(app, user_id, date=WED):
    with app.app_context():
        return sorted((b.start_time.strftime("%H:%M"), b.end_time.strftime("%H:%M"), b.text)
                      for b in WorkBlock.query.filter_by(user_id=user_id, date=date) if b.text)


def move(client, block_id, start, end, text, date="2026-11-11"):
    return client.post("/calendar/line", data={"date": date, "id": block_id, "start": start, "end": end,
                                               "text": text, "back": "/calendar/day/" + date}, follow_redirects=True)


def timesheet_spans(client, date="2026-11-11"):
    page = client.get("/timesheet/" + date).get_data(as_text=True)
    return re.findall(r'name="start" value="(\d\d:\d\d)".*?name="end" value="(\d\d:\d\d)"', page, re.S)


def test_moving_the_whole_line_takes_its_neighbours_along(app, client, three, user_id):
    move(client, three[1], "10:30", "11:30", "Payroll")
    assert lines(app, user_id) == [("09:00", "10:30", "VAT"), ("10:30", "11:30", "Payroll"),
                                   ("11:30", "12:00", "Emails")]
    assert timesheet_spans(client)[:3] == [("09:00", "10:30"), ("10:30", "11:30"), ("11:30", "12:00")]


def test_moving_the_top_edge(app, client, three, user_id):
    move(client, three[1], "09:45", "11:00", "Payroll")
    assert lines(app, user_id)[:2] == [("09:00", "09:45", "VAT"), ("09:45", "11:00", "Payroll")]


def test_moving_up_over_the_line_before(app, client, three, user_id):
    move(client, three[1], "09:30", "10:30", "Payroll")
    assert lines(app, user_id) == [("09:00", "09:30", "VAT"), ("09:30", "10:30", "Payroll"),
                                   ("10:30", "12:00", "Emails")]


def test_never_over_the_whole_of_a_neighbour(app, client, three, user_id):
    r = move(client, three[1], "11:00", "12:00", "Payroll")
    assert "run over the whole of the next line" in r.get_data(as_text=True)
    assert lines(app, user_id) == [("09:00", "10:00", "VAT"), ("10:00", "11:00", "Payroll"),
                                   ("11:00", "12:00", "Emails")]


def test_a_late_shift_moves_and_draws_past_midnight(app, client, user_id):
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=WED, login_time=T(15, 0), logout_time=T(1, 0)))
        late = WorkBlock(user_id=user_id, date=WED, start_time=T(23, 0), end_time=T(0, 0), text="Late")
        later = WorkBlock(user_id=user_id, date=WED, start_time=T(0, 0), end_time=T(0, 30), text="Later")
        db.session.add_all([late, later])
        db.session.commit()
        later_id = later.id
    move(client, later_id, "00:15", "00:45", "Later")
    assert lines(app, user_id) == [("00:15", "00:45", "Later"), ("23:00", "00:15", "Late")]
    grid = client.get("/calendar/day/2026-11-11").get_data(as_text=True)
    assert re.search(r'data-start="00:15" data-end="00:45"[^>]*\s+data-a="1455" data-b="1485"', grid)
    assert int(re.search(r'data-to="(\d+)"', grid).group(1)) >= 1500
    assert timesheet_spans(client)[-3:] == [("23:00", "00:15"), ("00:15", "00:45"), ("00:45", "01:00")]


def test_the_grid_has_zoom_grips_and_now(client, three, clock):
    page = client.get("/calendar/week/2026-11-11").get_data(as_text=True)
    assert [m for m in re.findall(r'data-zoom="(\d+)"', page)] == ["60", "30", "15", "5"]
    assert page.count('class="cg-grip cg-grip-start"') == 3 and page.count('class="cg-grip cg-grip-end"') == 3
    assert page.count('aria-describedby="cg-keys"') == 3 and 'id="cg-keys"' in page and 'id="cg-status"' in page
    # Thursday 12 November at 2 PM: today's column carries now, at 840.
    assert re.search(r'class="cg-now" data-now="840"', page)


def test_zoom_is_put_on_before_the_page_draws(client):
    page = client.get("/calendar/day").get_data(as_text=True)
    head = page.split("</head>", 1)[0]
    assert 'localStorage.getItem("stm-cal-zoom")' in head and "data-cal-zoom" in head
