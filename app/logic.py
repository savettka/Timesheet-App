"""
Core time-tracking calculations for STM.

Mirrors the rules from the user's original spreadsheet:
  - "Total hours" for a day = (logout - login) - actual break time taken,
    with overnight shifts (logout time-of-day earlier than login) rolling
    into the next day.
  - Standard/target hours default to 8h on workdays (Mon-Sat) and 0h on
    the rest day (Sun), for a 48h weekly target -- both configurable per
    user in Settings.
  - The week runs Monday -> Sunday. Once the running weekly total reaches
    the weekly target, no more hours are owed for that week (e.g. an
    early Saturday finish).
"""

from datetime import datetime, timedelta


def _combine(date_, time_, reference_dt=None):
    """Combine a date and a time-of-day into a datetime, rolling forward
    a day if the result would fall before ``reference_dt`` (handles shifts
    and breaks that cross midnight)."""
    dt = datetime.combine(date_, time_)
    if reference_dt is not None and dt < reference_dt:
        dt += timedelta(days=1)
    return dt


def break_segment_hours(entry, segment, now=None):
    """Hours for a single break segment. If the break is still open,
    counts up to ``now`` (or the current time)."""
    if segment.break_start is None:
        return 0.0
    start_dt = datetime.combine(entry.date, segment.break_start)
    if segment.break_end is not None:
        end_dt = _combine(entry.date, segment.break_end, reference_dt=start_dt)
    else:
        end_dt = now or datetime.now()
        if end_dt < start_dt:
            return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)


def entry_break_hours(entry, now=None):
    return sum(break_segment_hours(entry, b, now=now) for b in entry.breaks)


def closed_break_hours(entry):
    """Sum of only the completed (non-open) break segments."""
    return sum(
        break_segment_hours(entry, b) for b in entry.breaks if b.break_end is not None
    )


def entry_raw_hours(entry, now=None):
    """Login-to-logout span, ignoring breaks. Live (uses ``now``) if the
    entry is still open."""
    if entry.login_time is None:
        return 0.0
    login_dt = datetime.combine(entry.date, entry.login_time)
    if entry.logout_time is not None:
        logout_dt = _combine(entry.date, entry.logout_time, reference_dt=login_dt)
    else:
        logout_dt = now or datetime.now()
        if logout_dt < login_dt:
            return 0.0
    return max(0.0, (logout_dt - login_dt).total_seconds() / 3600.0)


def entry_total_hours(entry, now=None):
    """Total worked hours for the day, net of breaks (never negative)."""
    raw = entry_raw_hours(entry, now=now)
    brk = entry_break_hours(entry, now=now)
    return max(0.0, raw - brk)


def unrecorded_break_hours(user, entry_date, entry=None, now=None):
    """How much of the day's usual break is still to come.

    A clock-out suggestion has to allow for the break that will be taken but
    hasn't been logged yet, or it lands an hour early. Break time already
    recorded counts against the allowance, so the suggestion doesn't jump
    later the moment a real break is logged.
    """
    expected = user.expected_break_hours_for(entry_date.weekday())
    taken = entry_break_hours(entry, now=now) if entry is not None else 0.0
    return max(0.0, expected - taken)


def entry_target_hours(user, entry_date):
    """The default, weekday-based target for a date (ignores any override)."""
    return user.target_hours_for_weekday(entry_date.weekday())


def target_hours_for(user, entry_date, entry=None):
    """The effective target for a date: an explicit override on the entry
    (e.g. 0 for a day off, half the daily target for a half day) if one is
    set, otherwise the normal weekday-based default."""
    if entry is not None and entry.target_override is not None:
        return entry.target_override
    return entry_target_hours(user, entry_date)


def week_bounds(any_date):
    start = any_date - timedelta(days=any_date.weekday())  # Monday
    end = start + timedelta(days=6)  # Sunday
    return start, end


def week_dates(any_date):
    start, _ = week_bounds(any_date)
    return [start + timedelta(days=i) for i in range(7)]


def weekly_totals(user, entries_by_date, any_date, now=None):
    """Aggregate hours for the week containing ``any_date``.

    ``entries_by_date`` maps date -> TimeEntry for the relevant week.
    Returns a dict with worked/target/remaining hours and per-day rows.
    """
    days = week_dates(any_date)
    rows = []
    worked_total = 0.0
    # A day-off/half-day override shifts the weekly target by exactly the
    # difference from that day's usual target, so e.g. a full day of leave
    # on an 8h day relieves 8h from what's owed that week.
    target_delta = 0.0
    for d in days:
        entry = entries_by_date.get(d)
        default_target = entry_target_hours(user, d)
        target = target_hours_for(user, d, entry)
        if entry is not None and entry.target_override is not None:
            target_delta += target - default_target
        worked = entry_total_hours(entry, now=now) if entry else 0.0
        worked_total += worked
        rows.append(
            {
                "date": d,
                "entry": entry,
                "target_hours": target,
                "worked_hours": worked,
                "balance_hours": worked - target,
            }
        )

    target_total = max(0.0, user.weekly_target_hours + target_delta)
    remaining = max(0.0, target_total - worked_total)
    return {
        "week_start": days[0],
        "week_end": days[-1],
        "rows": rows,
        "worked_hours": worked_total,
        "target_hours": target_total,
        "remaining_hours": remaining,
        "complete": remaining <= 0,
        "progress_pct": min(100.0, (worked_total / target_total * 100.0) if target_total else 0.0),
    }


def saturday_plan(user, entries_by_date, week_start, weekly_target_total, today, now=None):
    """A forward-looking plan for when Saturday's shift can end, visible on
    any day of the week (not just Saturday itself) so it can be planned for
    in advance.

    Assumes the plan holds for every remaining weekday up to Friday (hits
    the normal/overridden target for today and any day still to come, keeps
    whatever actually happened on days already passed), then works out how
    much of the weekly target is left for Saturday. Once Saturday itself
    has actually started, switches to the exact live figure instead of a
    projection.

    Returns ``None`` if Saturday isn't a target day at all (e.g. the user
    doesn't work Saturdays and hasn't overridden it).
    """
    now = now or datetime.now()
    saturday_date = week_start + timedelta(days=5)
    sunday_date = week_start + timedelta(days=6)
    saturday_entry = entries_by_date.get(saturday_date)

    if target_hours_for(user, saturday_date, saturday_entry) <= 0:
        return None

    banked = 0.0
    for d in week_dates(week_start):
        if d in (saturday_date, sunday_date):
            continue
        entry = entries_by_date.get(d)
        if d < today:
            banked += entry_total_hours(entry, now=now) if entry else 0.0
        else:
            # Today or a day still to come: assume the plan holds.
            banked += target_hours_for(user, d, entry)
    if sunday_date < today:
        sunday_entry = entries_by_date.get(sunday_date)
        banked += entry_total_hours(sunday_entry, now=now) if sunday_entry else 0.0

    remaining_for_saturday = max(0.0, weekly_target_total - banked)

    if saturday_entry and saturday_entry.login_time and not saturday_entry.logout_time:
        reached, suggested_dt, still_needed = suggested_logout(
            saturday_entry, banked, weekly_target_total, now=now, user=user
        )
        return {
            "mode": "live",
            "saturday_date": saturday_date,
            "reached": reached,
            "suggested_time": fmt_suggested_datetime(suggested_dt, saturday_date),
            "still_needed_hours": still_needed,
        }

    if saturday_entry and saturday_entry.logout_time:
        worked = entry_total_hours(saturday_entry)
        return {
            "mode": "done",
            "saturday_date": saturday_date,
            "worked_hours": worked,
            "week_complete": (banked + worked) >= weekly_target_total,
        }

    projected_dt = None
    saturday_break = unrecorded_break_hours(user, saturday_date, saturday_entry, now=now)
    if remaining_for_saturday > 0 and user.saturday_login_hint:
        login_dt = datetime.combine(saturday_date, user.saturday_login_hint)
        # Sitting at the desk for the work plus the usual Saturday break.
        projected_dt = login_dt + timedelta(
            hours=remaining_for_saturday + saturday_break
        )

    return {
        "mode": "projection",
        "saturday_date": saturday_date,
        "remaining_hours": remaining_for_saturday,
        "projected_time": fmt_suggested_datetime(projected_dt, saturday_date),
        "has_login_hint": bool(user.saturday_login_hint),
        "reached": remaining_for_saturday <= 0,
    }


def fmt_clock(value):
    """A time of day on a 12-hour clock: 9:05 AM, 12:30 PM, 6:00 PM.

    Written out rather than using %I/%p so there's no leading zero and no
    dependence on the machine's locale.
    """
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


def fmt_suggested_datetime(dt, reference_date=None):
    """Format a suggested clock-out datetime for display, spelling out the
    date whenever it falls on a different day than ``reference_date``.

    Without this, a logout time that spills into tomorrow (e.g. someone is
    badly behind on hours) would render as a bare "HH:MM" and read as
    "later today" when it's actually a day or more away.
    """
    if dt is None:
        return None
    reference_date = reference_date or dt.date()
    if dt.date() == reference_date:
        return fmt_clock(dt)
    return f"{fmt_clock(dt)} on {dt.strftime('%a %d %b')}"


def suggested_logout(
    open_entry, weekly_before_today, weekly_target_hours, now=None, user=None
):
    """For a currently open (punched-in) entry, suggest a logout time that
    would exactly hit the weekly target, given hours already banked earlier
    in the week (``weekly_before_today``, i.e. not counting today).

    When ``user`` is given, the clock-out time also allows for whatever is
    left of the day's usual break, since that time will be spent but doesn't
    count as worked.

    Returns (already_reached: bool, suggested_dt: datetime | None,
    hours_remaining_after_now: float).
    """
    now = now or datetime.now()
    remaining_for_week = max(0.0, weekly_target_hours - weekly_before_today)

    today_worked_so_far = entry_total_hours(open_entry, now=now)
    if today_worked_so_far >= remaining_for_week:
        return True, now, 0.0

    still_needed = remaining_for_week - today_worked_so_far
    break_to_come = (
        unrecorded_break_hours(user, open_entry.date, open_entry, now=now)
        if user is not None
        else 0.0
    )
    suggested_dt = now + timedelta(hours=still_needed + break_to_come)
    return False, suggested_dt, still_needed


def fmt_hours(value):
    """Format decimal hours as 'Hh MMm'."""
    if value is None:
        return "--"
    sign = "-" if value < 0 else ""
    value = abs(value)
    total_minutes = round(value * 60)
    h, m = divmod(total_minutes, 60)
    return f"{sign}{h}h {m:02d}m"


def fmt_time(t):
    return fmt_clock(t) if t else "--:--"
