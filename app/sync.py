"""Keeping synced copies of STM in step with each other.

Every change to a day -- a login, a break, an edit, a delete -- goes through
the database session, so one hook sees all of them, however they're made and
including ones added to the app later:

* On the server, the hook gives each changed day a new version number from
  the user's counter, so a Windows app can ask for "everything since N".
* In the Windows app, the same hook hands over to the desktop package, which
  marks the day as waiting to be sent.

A day travels as one small JSON object (``day_payload``) and is written back
with ``apply_day``. Both live here so the two ends can never disagree about
what a day is made of.
"""

import math
from datetime import date, datetime, time, timedelta

import sqlalchemy as sa
from flask import current_app, has_app_context
from sqlalchemy import event

from app import db
from app.models import BreakSegment, DeletedDay, TimeEntry, User

# Set on a session while it writes days that arrived by sync, so they are not
# mistaken for fresh edits here and bounced straight back.
APPLYING = "stm_sync_applying"

# How far ahead of the server's clock an edit time may claim to be. A PC with
# its clock set wrong could otherwise make its edits win every clash forever.
CLOCK_SKEW_ALLOWANCE = timedelta(minutes=5)

# The desktop package plugs its own change handler in here.
desktop_change_hook = None


# ------------------------------------------------------------- day payloads

def _fmt_time(t):
    return t.strftime("%H:%M:%S") if t is not None else None


def _parse_time(value, field):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a time like 09:30")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a time like 09:30") from None


def day_payload(entry):
    """Everything about a day that syncs, as JSON-ready values. None for a
    day that doesn't exist."""
    if entry is None:
        return None
    return {
        "login_time": _fmt_time(entry.login_time),
        "logout_time": _fmt_time(entry.logout_time),
        "breaks": [[_fmt_time(b.break_start), _fmt_time(b.break_end)] for b in entry.breaks],
        "notes": entry.notes,
        "target_override": entry.target_override,
        "leave_label": entry.leave_label,
        "leave_type": entry.leave_type,
    }


def clean_payload(payload):
    """Check an incoming day and turn it into values ready to write.

    Raises ValueError with a readable reason for anything malformed, so one
    bad day is refused on its own instead of corrupting what's stored.
    """
    if not isinstance(payload, dict):
        raise ValueError("a day must be an object")

    breaks = []
    raw_breaks = payload.get("breaks") or []
    if not isinstance(raw_breaks, list) or len(raw_breaks) > 50:
        raise ValueError("breaks must be a list")
    for pair in raw_breaks:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("each break must be [start, end]")
        start = _parse_time(pair[0], "break start")
        if start is None:
            raise ValueError("each break needs a start time")
        breaks.append((start, _parse_time(pair[1], "break end")))

    target = payload.get("target_override")
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target):
            raise ValueError("target_override must be a number")
        target = max(0.0, float(target))

    leave_type = payload.get("leave_type")
    if leave_type not in (None, "half", "full", "custom"):
        raise ValueError("leave_type isn't one STM knows")

    def text(field, limit):
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text")
        return value[:limit] or None

    return {
        "login_time": _parse_time(payload.get("login_time"), "login_time"),
        "logout_time": _parse_time(payload.get("logout_time"), "logout_time"),
        "breaks": breaks,
        "notes": text("notes", 500),
        "target_override": target,
        "leave_label": text("leave_label", 60),
        "leave_type": leave_type,
    }


def parse_day(value):
    if not isinstance(value, str):
        raise ValueError("date must look like 2026-09-23")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must look like 2026-09-23") from None


def parse_edit_time(value, now=None):
    """An edit timestamp sent by a device: UTC, and never allowed to sit
    further in the future than a little clock drift explains."""
    now = now or datetime.utcnow()
    if not value:
        return now
    if not isinstance(value, str):
        raise ValueError("modified_at must be a date and time")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("modified_at must be a date and time") from None
    if stamp.tzinfo is not None:
        stamp = (stamp - stamp.utcoffset()).replace(tzinfo=None)
    return min(stamp, now + CLOCK_SKEW_ALLOWANCE)


def apply_day(session, user_id, day, clean, *, version, modified_at):
    """Write a day that arrived by sync: replace it with ``clean`` (from
    ``clean_payload``), or delete it when ``clean`` is None, stamped with the
    version and edit time it came with."""
    entry = session.query(TimeEntry).filter_by(user_id=user_id, date=day).first()
    tomb = session.query(DeletedDay).filter_by(user_id=user_id, date=day).first()

    if clean is None:
        if entry is not None:
            session.delete(entry)
        if tomb is None:
            tomb = DeletedDay(user_id=user_id, date=day)
            session.add(tomb)
        tomb.sync_version = version
        tomb.modified_at = modified_at
        return None

    if tomb is not None:
        session.delete(tomb)
    if entry is None:
        entry = TimeEntry(user_id=user_id, date=day)
        session.add(entry)
    entry.login_time = clean["login_time"]
    entry.logout_time = clean["logout_time"]
    entry.notes = clean["notes"]
    entry.target_override = clean["target_override"]
    entry.leave_label = clean["leave_label"]
    entry.leave_type = clean["leave_type"]
    entry.breaks.clear()
    for start, end in clean["breaks"]:
        entry.breaks.append(BreakSegment(break_start=start, break_end=end))
    entry.sync_version = version
    entry.modified_at = modified_at
    return entry


def user_payload(user):
    """The settings a Windows app needs to work out targets offline. They're
    changed on the web; the app only ever reads them."""
    return {
        "username": user.username,
        "display_name": user.display_name,
        "daily_target_hours": user.daily_target_hours,
        "weekly_target_hours": user.weekly_target_hours,
        "workdays": user.workdays,
        "saturday_login_hint": _fmt_time(user.saturday_login_hint),
        "weekday_break_minutes": user.weekday_break_minutes,
        "saturday_break_minutes": user.saturday_break_minutes,
    }


# ------------------------------------------------------------ change hook

def _entry_of(session, segment):
    """The day a break belongs to, even for a break mid-delete whose link to
    its day has already been cut."""
    if segment.entry is not None:
        return segment.entry
    entry_id = segment.entry_id
    if entry_id is None:
        history = sa.inspect(segment).attrs.entry_id.history
        entry_id = (history.deleted or [None])[0]
    return session.get(TimeEntry, entry_id) if entry_id is not None else None


def touched_days(session):
    """{(user_id, date): entry} for every day this flush changes -- entry is
    None when the day itself is being deleted."""
    touched = {}
    with session.no_autoflush:
        gone_users = {o.id for o in session.deleted if isinstance(o, User)}

        def note(entry, deleted=False):
            if entry is None or entry.user_id is None or entry.date is None:
                return
            if entry.user_id in gone_users:
                return  # the whole account is going; nothing left to sync
            key = (entry.user_id, entry.date)
            if deleted:
                touched.setdefault(key, None)
            else:
                touched[key] = entry

        for obj in session.deleted:
            if isinstance(obj, TimeEntry):
                note(obj, deleted=True)
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, TimeEntry) and (obj in session.new or session.is_modified(obj)):
                note(obj)
            elif isinstance(obj, BreakSegment) and (obj in session.new or session.is_modified(obj)):
                note(_entry_of(session, obj))
        for obj in session.deleted:
            if isinstance(obj, BreakSegment):
                entry = _entry_of(session, obj)
                if entry is not None and entry not in session.deleted:
                    note(entry)
    return touched


def next_version(conn, user_id):
    """Take the user's next change number. Done in SQL rather than on the
    loaded User so two saves can't be handed the same number; SQLite holds
    the write lock from here to commit, so numbers follow commit order."""
    users = User.__table__
    conn.execute(
        sa.update(users).where(users.c.id == user_id)
        .values(sync_counter=users.c.sync_counter + 1)
    )
    return conn.execute(sa.select(users.c.sync_counter).where(users.c.id == user_id)).scalar()


def _server_versions(session, touched):
    now = datetime.utcnow()
    conn = session.connection()
    tombs = DeletedDay.__table__
    versions = {}
    for (user_id, day), entry in touched.items():
        if user_id not in versions:
            versions[user_id] = next_version(conn, user_id)
        version = versions[user_id]
        conn.execute(sa.delete(tombs).where(tombs.c.user_id == user_id, tombs.c.date == day))
        if entry is None:
            conn.execute(sa.insert(tombs).values(
                user_id=user_id, date=day, sync_version=version, modified_at=now))
        else:
            entry.sync_version = version
            entry.modified_at = now


def _before_flush(session, flush_context, instances):
    if session.info.get(APPLYING) or not has_app_context():
        return
    touched = touched_days(session)
    if not touched:
        return
    if current_app.config.get("SYNC_ROLE", "server") == "desktop":
        if desktop_change_hook is not None:
            desktop_change_hook(session, touched)
    else:
        _server_versions(session, touched)


def init_sync_tracking():
    """Hook change tracking into the session, once per process."""
    if not event.contains(db.session, "before_flush", _before_flush):
        event.listen(db.session, "before_flush", _before_flush)
