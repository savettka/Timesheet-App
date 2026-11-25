"""A Timesheet line's words sit in a box that wraps and grows, so a long
entry shows in full; Enter still saves and a line break never goes in."""

import datetime as dt
import pathlib
import re

from app import db
from app.models import TimeEntry, WorkBlock

ROOT = pathlib.Path(__file__).resolve().parents[1]
LONG = ("Benbooks Visual Basic module testing for the VAT return checker and the bank feed rules "
        "for two clients, with notes for the team")


def test_a_long_entry_is_in_a_growing_box(app, client, user_id, clock):
    clock.set(2026, 11, 12, 15, 0)
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=dt.date(2026, 11, 12), login_time=dt.time(13, 0)))
        db.session.add(WorkBlock(user_id=user_id, date=dt.date(2026, 11, 12), start_time=dt.time(13, 0),
                                 end_time=dt.time(14, 0), text=LONG))
        db.session.commit()
    page = client.get("/timesheet").get_data(as_text=True)
    box = re.search(r'<textarea name="text" class="ts-text grow-text" rows="1"[^>]*>([^<]*)</textarea>', page)
    assert box and box.group(1) == LONG  # all of it, no line break before or after
    assert '<input type="text" name="text" class="ts-text"' not in page


def test_words_with_a_line_break_save_as_one_line(app, client, user_id, clock):
    clock.set(2026, 11, 12, 15, 0)
    with app.app_context():
        db.session.add(TimeEntry(user_id=user_id, date=dt.date(2026, 11, 12), login_time=dt.time(13, 0)))
        db.session.commit()
    client.post("/timesheet", data={"id": [""], "start": ["13:00"], "end": ["14:00"],
                                    "text": ["Payslips\r\nfor November"], "was_start": ["13:00"],
                                    "was_end": ["14:00"], "was_text": [""]})
    with app.app_context():
        assert [b.text for b in WorkBlock.query.all()] == ["Payslips for November"]


def test_the_script_grows_the_box_and_enter_saves():
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    grow = js[js.index("// ------------------------------------------------------------ long lines"):
              js.index("// ------------------------------------------------------------ slides")]
    assert "scrollHeight" in grow and "requestSubmit" in grow and "initGrowText();" in js
    assert 'textarea[data-suggest]' in js  # suggestions still offered in the box


def test_the_calendar_line_box_grows_too(client):
    page = client.get("/calendar/week").get_data(as_text=True)
    assert re.search(r'<textarea id="cg-text" name="text" class="grow-text" rows="1" maxlength="300"', page)
    assert '<input type="text" id="cg-text"' not in page
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    opened = js[js.index("function initCalendarGrid"):]
    assert "fitGrow(words)" in opened[:opened.index('getElementById("cg-cancel")')]
