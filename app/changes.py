"""Change history: each change to a day's login, logout, target, breaks and
Timesheet line times, as it's saved -- one hook, before every flush, so no
page can forget to write it. Where it came from (punch, edit, timer,
import, batch) is the page that made it; a punch with a time typed in is
an edit, by hand.

Only changes made on a page are kept (not scripts or the app starting up).
A Timesheet line's words, and lines added or removed, aren't: autosave
would bury the times in them.
"""

from datetime import datetime, time
from types import SimpleNamespace

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import event, inspect

from app import db, logic
from app.models import BreakSegment, EntryChange, TimeEntry, WorkBlock

FIELDS = {
    TimeEntry: (("login_time", "Login"), ("logout_time", "Logout"), ("target_override", "Target")),
    BreakSegment: (("break_start", "Break start"), ("break_end", "Break end")),
    WorkBlock: (("start_time", "Line start"), ("end_time", "Line end")),
}
# What marks a day as typed by hand, when the source was an edit.
PUNCH_FIELDS = ("Login", "Logout", "Break start", "Break end", "Break removed", "Day deleted")
SOURCES = {
    "main.punch_in": "punch", "main.punch_out": "punch",
    "main.punch_break_start": "punch", "main.punch_break_end": "punch",
    "main.timer_start": "timer", "main.timer_stop": "timer", "main.timer_idle": "timer",
    "main.timesheet_batch": "batch",
    "main.timesheet_week_copy_last": "import", "main.timesheet_template_use": "import",
}
SOURCE_WORDS = {"punch": "By button", "edit": "By hand", "timer": "Timer", "import": "Copied in", "batch": "Batch"}


def source():
    """Where a change comes from: the page that made it."""
    found = SOURCES.get(request.endpoint, "edit")
    if found == "punch" and (request.form.get("time") or "").strip():
        return "edit"  # a time typed in, not the clock's
    return found


def shown(value):
    """A value as kept: times to the minute, so a day saved again from its
    form (which has no seconds) isn't a change."""
    if value is None:
        return ""
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, float):
        return f"{value:g}h"
    return str(value)


def _owner(session, obj):
    """The user and day a changed row belongs to."""
    if isinstance(obj, BreakSegment):
        entry = obj.entry
        if entry is None and obj.entry_id is not None:
            with session.no_autoflush:
                entry = session.get(TimeEntry, obj.entry_id)
        return (entry.user_id, entry.date) if entry is not None else (None, None)
    return obj.user_id, obj.date


def record(session, flush_context, instances):
    if not has_request_context() or not current_user or not current_user.is_authenticated:
        return
    where, now, rows = source(), datetime.now(), []

    def add(obj, field, old, new):
        user_id, day = _owner(session, obj)
        if user_id is not None and day is not None and old != new:
            rows.append(EntryChange(user_id=user_id, entry_date=day, field=field, old_value=old,
                                    new_value=new, source=where, changed_at=now))

    for obj in session.new:
        if isinstance(obj, TimeEntry):
            for attr, field in FIELDS[TimeEntry]:
                if getattr(obj, attr) is not None:
                    add(obj, field, "", shown(getattr(obj, attr)))

    # Breaks: the day's form puts every break back each time it's saved, so
    # a new break is only a change if the day didn't have it already; and
    # one left out of the day's breaks (deleted on the way) has gone.
    added = {}
    for obj in session.new:
        if isinstance(obj, BreakSegment):
            entry = obj.entry
            if entry is None and obj.entry_id is not None:
                with session.no_autoflush:
                    entry = session.get(TimeEntry, obj.entry_id)
            added.setdefault(entry, []).append(obj)
    for obj in session.dirty:
        if isinstance(obj, TimeEntry):
            added.setdefault(obj, [])
    for entry, new in added.items():
        if entry is None:
            continue
        before = []
        if entry.id is not None:
            with session.no_autoflush:
                before = session.query(BreakSegment).filter_by(entry_id=entry.id).all()
        old_spans = {(shown(b.break_start), shown(b.break_end)) for b in before}
        new_spans = {(shown(b.break_start), shown(b.break_end)) for b in new}
        for b in new:
            if (shown(b.break_start), shown(b.break_end)) not in old_spans:
                for attr, field in FIELDS[BreakSegment]:
                    if getattr(b, attr) is not None:
                        add(entry, field, "", shown(getattr(b, attr)))
        for b in before:
            span = (shown(b.break_start), shown(b.break_end))
            if b not in entry.breaks and b not in session.deleted and span not in new_spans:
                add(entry, "Break removed", f"{span[0]}–{span[1]}", "")

    for obj in session.dirty:
        if type(obj) not in FIELDS or not session.is_modified(obj):
            continue
        state = inspect(obj)
        for attr, field in FIELDS[type(obj)]:
            history = state.attrs[attr].history
            if history.has_changes():
                old = history.deleted[0] if history.deleted else None
                new = history.added[0] if history.added else None
                add(obj, field, shown(old), shown(new))
    for obj in session.deleted:
        if isinstance(obj, TimeEntry):
            add(obj, "Day deleted", shown(obj.login_time) or "leave", "")
        elif isinstance(obj, BreakSegment) and (obj.entry is None or obj.entry not in session.deleted):
            add(obj, "Break removed", f"{shown(obj.break_start)}–{shown(obj.break_end)}", "")
    session.add_all(rows)


def install():
    """The hook, on Flask-SQLAlchemy's session -- once, however many apps."""
    if not event.contains(db.session, "before_flush", record):
        event.listen(db.session, "before_flush", record)


def edited_days(user_id, first, last):
    """The days from first to last with punch times typed by hand."""
    rows = (db.session.query(EntryChange.entry_date).distinct()
            .filter(EntryChange.user_id == user_id, EntryChange.entry_date >= first,
                    EntryChange.entry_date <= last, EntryChange.source == "edit",
                    EntryChange.field.in_(PUNCH_FIELDS)))
    return {day for day, in rows}


def pretty(value):
    """A kept value for the page: 18:05 as 6:05 PM, a break as 1:00 PM–2:00 PM."""
    def one(part):
        try:
            return logic.fmt_clock(datetime.strptime(part, "%H:%M").time())
        except ValueError:
            return part
    return "–".join(one(part) for part in value.split("–")) if value else ""


def day_history(user_id, day):
    """A day's changes, newest first, ready to show."""
    rows = (EntryChange.query.filter_by(user_id=user_id, entry_date=day)
            .order_by(EntryChange.changed_at.desc(), EntryChange.id.desc()).all())
    return [SimpleNamespace(changed_at=c.changed_at, field=c.field, source=c.source,
                            old_value=pretty(c.old_value), new_value=pretty(c.new_value)) for c in rows]
