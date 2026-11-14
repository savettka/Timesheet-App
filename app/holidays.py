"""Public holidays: the built-in Indian list (static/data/holidays-in.json,
read from the repo -- no call out), lines pasted in, or one at a time.

Saving one sets that day's target to 0 -- as a day of leave named for the
holiday -- unless the day already has a target of its own, so every figure
(the week's target, the month's balance, the heatmap, goals) sees it with
no change to how they're worked out. Taking it away undoes only what
saving it did.

Also the leave calendar: leave days and holidays as an .ics feed, for
Outlook or Google Calendar to subscribe to.
"""

import json
import os
from datetime import date, datetime, timedelta

from flask import current_app

from app import db
from app.models import Holiday, TimeEntry

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")
MOST_LINES = 200


def _data():
    path = os.path.join(current_app.static_folder, "data", "holidays-in.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def builtin(year):
    """The built-in list for a year, as (date, name, ticked) -- ticked to
    start with: the national three, and the rest when the list is the
    office's own -- and where it's from, or None for a year the file has
    no list for, which gets only the national three."""
    data = _data()
    national = {date(year, n["month"], n["day"]): n["name"] for n in data["national"]}
    known = data.get("years", {}).get(str(year))
    if not known:
        return [(d, name, True) for d, name in sorted(national.items())], None
    office = bool(known.get("office"))
    found = []
    for h in known["holidays"]:
        d = date.fromisoformat(h["date"])
        found.append((d, h["name"], d in national or office))
    about = {k: known.get(k) for k in ("source", "checked", "region")}
    if about["checked"]:
        about["checked"] = f"{date.fromisoformat(about['checked']):%d %b %Y}"
    return sorted(found), about


def parse_date(text):
    text = " ".join((text or "").split())
    for form in DATE_FORMATS:
        try:
            return datetime.strptime(text, form).date()
        except ValueError:
            continue
    return None


def parse_lines(text):
    """Holidays pasted in, one a line: "2026-12-25, Christmas" -- or with a
    tab, or 25/12/2026, or 25 Dec 2026. Returns (date, name) pairs, and
    the lines it couldn't read."""
    found, bad = [], []
    for raw in (text or "").splitlines()[:MOST_LINES]:
        line = raw.strip()
        if not line:
            continue
        cut = min((i for i in (line.find(","), line.find("\t")) if i >= 0), default=-1)
        when, name = (line[:cut], line[cut + 1:]) if cut >= 0 else (line, "")
        day = parse_date(when)
        name = " ".join(name.split())[:Holiday.MAX_NAME]
        if day is None or not name or not 1970 <= day.year <= 2100:
            bad.append(line[:60])
        else:
            found.append((day, name))
    return found, bad


def _ours(entry, holiday):
    """Whether a day's target is the one saving this holiday set."""
    return (entry is not None and holiday.applied and entry.target_override == 0
            and entry.leave_label == holiday.name)


def save(user, chosen):
    """Holidays saved (a date already saved takes the new name), each day's
    target set to 0 where it has none of its own. Returns how many are new."""
    saved = {h.date: h for h in Holiday.query.filter_by(user_id=user.id)}
    new = 0
    for day, name in chosen:
        holiday = saved.get(day)
        entry = TimeEntry.query.filter_by(user_id=user.id, date=day).first()
        if holiday is None:
            holiday = saved[day] = Holiday(user_id=user.id, date=day, name=name, applied=False)
            db.session.add(holiday)
            new += 1
        elif _ours(entry, holiday):
            entry.leave_label = name  # renamed: its day follows
        holiday.name = name
        if entry is None or entry.target_override is None:
            if entry is None:
                entry = TimeEntry(user_id=user.id, date=day)
                db.session.add(entry)
            entry.target_override, entry.leave_type, entry.leave_label = 0.0, "full", name
            holiday.applied = True
    return new


def remove(user, holiday):
    """A holiday taken away, and its day back as it was before saving it."""
    entry = TimeEntry.query.filter_by(user_id=user.id, date=holiday.date).first()
    if _ours(entry, holiday):
        if entry.login_time is None and not entry.notes:
            db.session.delete(entry)
        else:
            entry.target_override = entry.leave_type = entry.leave_label = None
    db.session.delete(holiday)


# ------------------------------------------------------------- the feed

def _escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def _fold(line):
    """A calendar line cut at 75 bytes, as the format asks."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75 if not out else 74
        while cut and (raw[cut] & 0xC0) == 0x80:  # never inside a character
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


def ics(user, today):
    """Leave days and holidays from a year back to two ahead, as a calendar
    of all-day events."""
    first, last = today - timedelta(days=366), today + timedelta(days=731)
    days = {}
    for e in TimeEntry.query.filter(TimeEntry.user_id == user.id, TimeEntry.date >= first, TimeEntry.date <= last,
                                    TimeEntry.target_override.isnot(None)):
        days[e.date] = e.leave_label or TimeEntry.LEAVE_TYPES.get(e.leave_type) or "Leave"
    for h in Holiday.query.filter(Holiday.user_id == user.id, Holiday.date >= first, Holiday.date <= last):
        days[h.date] = h.name
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//STM//Leave and holidays//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:STM leave and holidays"]
    for day, name in sorted(days.items()):
        lines += ["BEGIN:VEVENT", f"UID:stm-{user.id}-{day:%Y%m%d}@stm", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_escape(name)}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
