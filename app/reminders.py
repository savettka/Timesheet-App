"""Reminders shown in STM while a page of it is open: a bar at the top of
any page (app.js, initReminders, from /api/reminders), and a browser
notification where it's been allowed. Email only carries the weekly
report -- it can't say anything on the day.

  no_login    no login yet by a set time, on a workday
  long_day    still logged in after a set number of hours worked
  long_break  breaks today past the usual allowance (Settings)
  unwritten   the last day worked has Timesheet lines still to fill in
  idle        the idle prompt (app.js): minutes away from the page before
              asking -- settings only; nothing to work out here
"""

import json
from datetime import timedelta

from app import logic
from app.models import TimeEntry, WorkBlock

DEFAULTS = {
    "no_login": {"on": True, "at": 10 * 60},
    "long_day": {"on": True, "hours": 10.0},
    "long_break": {"on": True},
    "unwritten": {"on": True},
    "idle": {"on": False, "minutes": 15},
}
NAMES = {
    "no_login": "Not logged in by",
    "long_day": "Still logged in after",
    "long_break": "Break over the allowance",
    "unwritten": "Last day's lines not written",
    "idle": "Away from the timer for",
}
LIMITS = {"at": (0, 1439), "hours": (1, 24), "minutes": (5, 240)}


def settings_for(user):
    """Each reminder's settings: what's saved, over the defaults. Anything
    unreadable falls back to its default."""
    try:
        saved = json.loads(user.reminder_settings or "{}")
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    out = {}
    for key, default in DEFAULTS.items():
        mine = saved.get(key) if isinstance(saved.get(key), dict) else {}
        out[key] = {"on": bool(mine.get("on", default["on"]))}
        for field in default:
            if field == "on":
                continue
            value = mine.get(field, default[field])
            low, high = LIMITS[field]
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high
            out[key][field] = value if ok else default[field]
    return out


def due(user, now, open_entry):
    """The reminders that apply right now, each with a key (the reminder and
    its day, for the page to remember one put away), its words, and where
    it leads."""
    on = settings_for(user)
    today = logic.work_day(open_entry, now)
    found = []

    if on["no_login"]["on"] and open_entry is None:
        entry = TimeEntry.query.filter_by(user_id=user.id, date=today).first()
        logged = entry is not None and entry.login_time is not None
        if (not logged and logic.target_hours_for(user, today, entry) > 0
                and now.hour * 60 + now.minute >= on["no_login"]["at"]):
            found.append(("no_login", today, "You haven't logged in yet today.", "/dashboard"))

    live = open_entry is not None and not logic.is_stale_shift(open_entry, now)
    if live and on["long_day"]["on"]:
        worked = logic.entry_total_hours(open_entry, now=now)
        if worked >= on["long_day"]["hours"]:
            found.append(("long_day", open_entry.date,
                          f"Still logged in after {logic.fmt_duration(worked)} worked.", "/dashboard"))

    if live and on["long_break"]["on"] and open_entry.open_break() is not None:
        allowance = user.expected_break_hours_for(open_entry.date.weekday())
        taken = logic.entry_break_hours(open_entry, now=now)
        if allowance > 0 and taken > allowance:
            found.append(("long_break", open_entry.date,
                          f"On a break: {logic.fmt_duration(taken)} today, over your "
                          f"{logic.fmt_duration(allowance)}.", "/dashboard"))

    if on["unwritten"]["on"]:
        last = (TimeEntry.query.filter(TimeEntry.user_id == user.id, TimeEntry.date < today,
                                       TimeEntry.date >= today - timedelta(days=7),
                                       TimeEntry.login_time.isnot(None))
                .order_by(TimeEntry.date.desc()).first())
        if last is not None and not logic.is_stale_shift(last, now):
            blocks = WorkBlock.query.filter_by(user_id=user.id, date=last.date).all()
            empty = sum(1 for _, _, block in logic.timesheet_lines(last, last.date, now, blocks) if block is None)
            if empty:
                day = "Yesterday" if last.date == today - timedelta(days=1) else f"{last.date:%a %d %b}"
                found.append(("unwritten", last.date,
                              f"{day}'s timesheet has {empty} line{'s' if empty != 1 else ''} to fill in.",
                              f"/timesheet/{last.date.isoformat()}"))

    return [{"key": f"{key}:{day.isoformat()}", "text": text, "href": href} for key, day, text, href in found]
