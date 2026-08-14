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


def suggested_logout(open_entry, weekly_before_today, weekly_target_hours, now=None):
    """For a currently open (punched-in) entry, suggest a logout time that
    would exactly hit the weekly target, given hours already banked earlier
    in the week (``weekly_before_today``, i.e. not counting today).

    Returns (already_reached: bool, suggested_dt: datetime | None,
    hours_remaining_after_now: float).
    """
    now = now or datetime.now()
    remaining_for_week = max(0.0, weekly_target_hours - weekly_before_today)

    today_worked_so_far = entry_total_hours(open_entry, now=now)
    if today_worked_so_far >= remaining_for_week:
        return True, now, 0.0

    still_needed = remaining_for_week - today_worked_so_far
    # Assumes no further breaks between now and logout.
    suggested_dt = now + timedelta(hours=still_needed)
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
    return t.strftime("%H:%M") if t else "--:--"
